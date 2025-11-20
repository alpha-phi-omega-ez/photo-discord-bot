import os
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from logging import INFO, Formatter, Logger, StreamHandler, getLogger
from os import getenv, unlink
from re import compile as re_compile
from tempfile import NamedTemporaryFile
from time import sleep
from typing import Any

import aiofiles
import sentry_sdk
from discord import (
    Forbidden,
    Intents,
    Interaction,
    Member,
    NotFound,
    Thread,
    app_commands,
    errors,
    message,
    utils,
)
from discord.ext import commands
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from PIL import Image
from psutil import virtual_memory
from pyheif import read as pyheif_read
from requests import get, head
from sentry_sdk.integrations.aiohttp import AioHttpIntegration
from sentry_sdk.integrations.stdlib import StdlibIntegration

# Load environment variables from .env file
load_dotenv()

# Setup Sentry
sentry_sdk.init(
    dsn=getenv("SENTRY_DSN", ""),
    integrations=[
        AioHttpIntegration(),  # For async HTTP requests
        StdlibIntegration(),  # For threading-related error tracking
    ],
    traces_sample_rate=float(getenv("SENTRY_TRACE_RATE", 1.0)),
)

# Program variables
DISCORD_TOKEN = getenv("DISCORD_TOKEN")
CHANNEL_NAME = getenv("CHANNEL_NAME")
SHARED_DRIVE_ID = getenv("SHARED_DRIVE_ID")
GUILD_ID = getenv("GUILD_ID")
VIDEO_IN_MEMORY = getenv("VIDEO_IN_MEMORY", "False").lower() == "true"
DELEGATE_EMAIL = getenv("DELEGATE_EMAIL")
LOG_LEVEL = getenv("LOG_LEVEL", "INFO").upper()
ROLE_NAME = getenv("ROLE_NAME")
PARENT_FOLDER_ID = None
# Exponential backoff delays: 2s, 6s, 18s, 54s, 120s (capped)
EXPONENTIAL_BACKOFF_DELAYS = [2.0, 6.0, 18.0, 54.0, 120.0]
parent_folder_file = "config/parent_folder_id.txt"
if os.path.exists(parent_folder_file):
    print("Reading parent folder ID from file")
    with open(parent_folder_file, "r") as f:
        PARENT_FOLDER_ID = f.read().strip()
else:
    print("No parent folder ID file found, using environment variable")
    PARENT_FOLDER_ID = getenv("PARENT_FOLDER_ID")

# Exit if any critical variables are None
if not all(
    [
        DISCORD_TOKEN,
        PARENT_FOLDER_ID,
        CHANNEL_NAME,
        SHARED_DRIVE_ID,
        GUILD_ID,
        DELEGATE_EMAIL,
        ROLE_NAME,
    ]
):
    missing_vars = [
        var
        for var, value in {
            "DISCORD_TOKEN": DISCORD_TOKEN,
            "PARENT_FOLDER_ID": PARENT_FOLDER_ID,
            "CHANNEL_NAME": CHANNEL_NAME,
            "SHARED_DRIVE_ID": SHARED_DRIVE_ID,
            "GUILD_ID": GUILD_ID,
            "DELEGATE_EMAIL": DELEGATE_EMAIL,
            "ROLE_NAME": ROLE_NAME,
        }.items()
        if not value
    ]
    print("Missing environment variables:", ", ".join(missing_vars))
    exit(1)


# Setup Discord intents
intents = Intents.default()
intents.message_content = True
intents.guilds = True
intents.message_content = True

GUILD = None

# Global variables
IMAGE_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".heic",
    ".heif",
)
VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".mkv")
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]
IMAGE_NAME_PATTERN = re_compile(r"([\w]+\.(?:png|jpg|jpeg|heic|heif))")
VIDEO_NAME_PATTERN = re_compile(r"([\w]+\.(?:mp4|mov|avi|mkv))")

# Google service
SERVICE = None

# Discord commands bot
bot = commands.Bot(command_prefix="!", intents=intents)

# Create a thread pool for downloading images
EXECUTOR = ThreadPoolExecutor(max_workers=1)

logger = getLogger("photo-bot")


def setup_logger(logger_setup: Logger, log_level=INFO) -> None:
    """Setup logger for the bot."""
    logger_setup.setLevel(log_level)

    getLogger("discord.http").setLevel(log_level)
    handler = StreamHandler()
    formatter = Formatter(
        (
            "\x1b[30;1m%(asctime)s\x1b[0m "
            "\x1b[34;1m%(levelname)-8s\x1b[0m "
            "\x1b[35m%(name)s\x1b[0m %(message)s"
        ),
        "%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger_setup.addHandler(handler)


def get_file_size(url: str) -> int | None:
    """Returns the file size in bytes from a URL."""
    try:
        response = head(url)
        if response.status_code == 200 and "Content-Length" in response.headers:
            logger.debug(f"File size for {url}: {response.headers['Content-Length']}")
            return int(response.headers["Content-Length"])
        else:
            logger.debug("Could not retrieve file size.")
            return None
    except Exception as e:
        logger.error(f"Failed to get file size: {e}")
        return None


def is_memory_available(file_size: int) -> bool:
    """Check if enough memory is available for the given file size."""
    available_memory = virtual_memory().available

    logger.debug(f"Available memory: {available_memory}")

    return (available_memory - 20000) > file_size


def authenticate_google_drive() -> Any:
    """Authenticate the user and return a service object"""
    logger.info("authenticating google cloud service account")
    creds = Credentials.from_service_account_file(
        "config/service-credentials.json", scopes=SCOPES
    )
    delegated_creds = creds.with_subject(DELEGATE_EMAIL)

    logger.info("creating google cloud service")

    # Create the Google Drive API service
    service = build("drive", "v3", credentials=delegated_creds)

    return service


def check_parent_folder_id(folder_id: str) -> bool:
    # Verify the folder ID exists in Google Drive
    try:
        if not SERVICE:
            raise Exception("Google Drive service not authenticated")

        _ = (
            SERVICE.files()
            .get(fileId=folder_id, supportsAllDrives=True, fields="id")
            .execute()
        )
        return True
    except Exception as e:
        logger.warning(f"Invalid folder ID provided: {folder_id} - {e}")
    return False


def check_folder_exists(folder_name: str) -> str | None:
    try:
        if not SERVICE:
            raise Exception("Google Drive service not authenticated")

        logger.debug(f"Searching for folder: {folder_name}")

        response = None
        for attempt in range(5):
            try:
                # Search for the folder in the specified shared drive
                # using the folder name and parent folder ID
                response = (
                    SERVICE.files()
                    .list(
                        # Query to filter by folder parent ID and name
                        q=f"'{PARENT_FOLDER_ID}' in parents and name='{folder_name}'",
                        corpora="drive",
                        driveId=SHARED_DRIVE_ID,
                        includeItemsFromAllDrives=True,
                        supportsAllDrives=True,
                    )
                    .execute()
                )
                break
            except Exception as e:
                logger.debug(f"Failed to find folder: {e}")
                if attempt < 4:  # Don't sleep after last attempt
                    sleep(EXPONENTIAL_BACKOFF_DELAYS[attempt])

        # Check if the folder exists in the response
        if response is None:
            logger.debug(f"Failed to find folder: {folder_name}")
            return None
        folders = response.get("files", [])
        if folders:
            return folders[0].get("id")

        logger.debug(f"Failed to find folder: {folder_name}")
    except Exception as e:
        logger.error(f"Failed to find folder: {e}")
    return None


def create_folder(folder_name) -> str | None:
    try:
        if not SERVICE:
            raise Exception("Google Drive service not authenticated")

        # Define the metadata for the new folder
        folder_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [PARENT_FOLDER_ID],  # Set the parent folder in the shared drive
        }

        logger.debug(f"Creating folder: {folder_name}")

        new_folder = None
        for attempt in range(5):
            try:
                # Create the new folder in the specified shared drive folder
                new_folder = (
                    SERVICE.files()
                    .create(
                        body=folder_metadata,
                        supportsAllDrives=True,  # Ensure it supports shared drives
                        fields="id, name",
                    )
                    .execute()
                )
                break
            except Exception as e:
                logger.debug(f"Failed to create folder: {e}")
                if attempt < 4:  # Don't sleep after last attempt
                    sleep(EXPONENTIAL_BACKOFF_DELAYS[attempt])

        if new_folder:
            return new_folder.get("id")

        logger.error(f"Failed to create folder: {folder_name}")
    except Exception as e:
        logger.error(f"Failed to create folder: {e}")
    return None


def convert_to_jpeg(image_data, file_name, extension):
    try:
        logger.debug("Converting HEIC/HEIF image")
        heif_file = pyheif_read(BytesIO(image_data))

        # Convert to a Pillow Image object
        image = Image.frombytes(
            heif_file.mode,
            heif_file.size,
            heif_file.data,
            "raw",
            heif_file.mode,
            heif_file.stride,
        )

        # Save the image to a BytesIO object in JPEG format
        img_bytes = BytesIO()
        image.save(img_bytes, format="JPEG")
        new_image_data = img_bytes.getvalue()
        new_extension = "jpeg"
        new_file_name = file_name.replace("heic", "jpeg")

        logger.debug("Converted HEIC/HEIF image")

        return new_image_data, new_file_name, new_extension
    except Exception as e:
        logger.debug(f"Failed to convert HEIC/HEIF image: {e}")
        return image_data, file_name, extension


def upload(
    folder_id, stream_data, file_name, extension, thread_name, file_type, file_path=None
) -> None:
    try:
        if not SERVICE:
            raise Exception("Google Drive service not authenticated")

        # Define metadata for the new file
        file_metadata = {
            "name": file_name.upper(),
            "parents": [folder_id],  # Specify the parent folder ID
        }

        if extension == "jpg":
            extension = "jpeg"

        media = None

        # Determine the MIME type based on the file type
        if stream_data:
            media = MediaIoBaseUpload(
                stream_data, mimetype=f"{file_type}/{extension}", resumable=True
            )
        elif file_path:
            media = MediaFileUpload(
                file_path, mimetype=f"{file_type}/{extension}", resumable=True
            )

        if media:
            uploaded_file = None
            for attempt in range(5):
                try:
                    # Upload the file to Google drive
                    uploaded_file = (
                        SERVICE.files()
                        .create(
                            body=file_metadata,
                            media_body=media,
                            # Ensures compatibility with shared drives
                            supportsAllDrives=True,
                            fields="id, name",
                        )
                        .execute()
                    )
                    break
                except Exception as e:
                    logger.debug(f"Failed to upload image: {e}")
                    if attempt < 4:  # Don't sleep after last attempt
                        sleep(EXPONENTIAL_BACKOFF_DELAYS[attempt])
        else:
            logger.error("No media data to upload")
            return

        # Check if the upload was successful
        if uploaded_file:
            logger.info(
                f"Uploaded {file_name} to {thread_name}, "
                "File ID: {uploaded_file.get('id')}"
            )
        else:
            logger.warning(f"Failed to upload image: {file_name.upper()}")
        sleep(1)
    except Exception as e:
        logger.error(f"Failed to upload image: {e}")


def download_image(url, file_name, folder_id, extension, thread_name) -> None:
    try:
        logger.debug(f"Downloading image from {url}")
        for attempt in range(5):
            try:
                # Request the image data
                response = get(url)
                logger.debug(f"Response {url}: {response.status_code}")

                if response.status_code == 200:
                    # Get the image data
                    image_data = response.content

                    logger.debug(f"Downloaded image from {url}")

                    # Check if the image is HEIC/HEIF and convert to JPEG
                    if "heic" == extension or "heif" == extension:
                        image_data, file_name, extension = convert_to_jpeg(
                            image_data, file_name, extension
                        )

                    # Use BytesIO as an in-memory file to store the download stream
                    image_data_bytes = BytesIO(image_data)

                    upload(
                        folder_id,
                        image_data_bytes,
                        file_name,
                        extension,
                        thread_name,
                        "image",
                    )
                    return
                else:
                    logger.debug(f"Failed to download image from {url}")
            except Exception as e:
                logger.debug(f"Failed to download image: {e}")
                if attempt < 4:  # Don't sleep after last attempt
                    sleep(EXPONENTIAL_BACKOFF_DELAYS[attempt])

        logger.error(f"Failed to download image: {url}")
    except Exception as e:
        logger.error(f"Failed to download image: {e}")


def download_video(folder_id, url, file_name, extension, thread_name) -> None:
    try:
        logger.debug(f"Downloading video from {url}")

        for attempt in range(5):
            try:
                # Get the file size from the URL
                file_size = get_file_size(url)

                logger.debug(f"File size: {file_size}")

                # Request the video data
                response = get(url, stream=True)
                logger.debug(f"Response {url}: {response.status_code}")

                if response.status_code == 200:
                    logger.debug(f"Downloaded video from {url}")

                    # Check if the file size is available and if memory is available
                    if VIDEO_IN_MEMORY and file_size and is_memory_available(file_size):
                        logger.debug(f"Downloading video from {url} to memory")

                        # Use BytesIO as an in-memory file to store the download stream
                        video_stream = BytesIO()

                        # Write the video content to the stream in chunks
                        for chunk in response.iter_content(chunk_size=8192):
                            video_stream.write(chunk)

                        # Reset the stream position to the start
                        video_stream.seek(0)

                        logger.debug("Completed download to memory")

                        upload(
                            folder_id,
                            video_stream,
                            file_name,
                            extension,
                            thread_name,
                            "video",
                        )
                        return

                    # If not enough memory, download to disk
                    else:
                        logger.debug(f"Downloading video from {url} to disk")

                        # Create a temporary file with 'wb+' mode to read/write binary
                        temp_file = NamedTemporaryFile(
                            delete=False, suffix=f".{extension}"
                        )

                        # Write the video content to the temp file in chunks
                        for chunk in response.iter_content(chunk_size=8192):
                            temp_file.write(chunk)

                        temp_file.flush()  # Ensure all data is written
                        temp_file.seek(
                            0
                        )  # Move to the beginning of the file for reading

                        logger.debug(f"Completed download to disk: {temp_file.name}")

                        if temp_file:
                            try:
                                # Upload the video file from the temp file
                                upload(
                                    folder_id,
                                    None,
                                    file_name,
                                    extension,
                                    thread_name,
                                    "video",
                                    temp_file.name,
                                )
                            finally:
                                temp_file.close()  # Close the file
                                unlink(temp_file.name)
                                return
                        else:
                            logger.error("Failed to download video")
                            return
                else:
                    logger.debug(f"Failed to download image from {url}")
            except Exception as e:
                logger.debug(f"Failed to download image: {e}")
                if attempt < 4:  # Don't sleep after last attempt
                    sleep(EXPONENTIAL_BACKOFF_DELAYS[attempt])
    except Exception as e:
        logger.error(f"Failed to download video: {e}")


def find_file_name(pattern, url) -> str | None:
    """Find the file name from the URL using regex pattern."""
    try:
        return pattern.findall(url)[0].replace(" ", "_").replace("'", "\x27")
    except Exception as e:
        logger.debug(f"Failed to find file name: {e}")
    return None


def queue_file_downloads(thread_name, attachments, folder_id=None) -> None:
    """Queue the file downloads for images and videos."""
    try:
        thread_name = thread_name.replace("'", "\x27")
        logger.debug(f"Thread Name: {thread_name}")

        # Check if the folder ID is provided, if not, check if it exists
        if folder_id is None:
            folder_id = check_folder_exists(thread_name)
            if folder_id is None:
                folder_id = create_folder(thread_name)

        logger.info(f"FOLDER ID: {folder_id}")

        if not folder_id:
            logger.debug("Missing folder ID")
            return

        # Iterate through the attachments
        for attachment in attachments:
            url_lower = attachment.url.lower()

            logger.debug(f"Attachment URL: {attachment.url}")

            # Check if the URL contains image or video extensions
            if any(ext in url_lower for ext in IMAGE_EXTENSIONS):
                logger.debug(f"Found image attachment: {attachment.url}")

                file_name = find_file_name(IMAGE_NAME_PATTERN, url_lower)

                if file_name is None:
                    logger.debug("Could not image file name")
                    continue

                logger.debug(f"Found image name: {file_name}")

                # Queue the download task
                EXECUTOR.submit(
                    download_image,
                    attachment.url,
                    file_name,
                    folder_id,
                    file_name.split(".")[-1],
                    thread_name,
                )

            # Check if the URL contains video extensions
            elif any(ext in url_lower for ext in VIDEO_EXTENSIONS):
                logger.debug(f"Found video attachment: {attachment.url}")

                file_name = find_file_name(VIDEO_NAME_PATTERN, url_lower)

                if file_name is None:
                    logger.debug("Could not find video file name")
                    continue

                logger.debug(f"Found video name: {file_name}")

                # Queue the download task
                EXECUTOR.submit(
                    download_video,
                    folder_id,
                    attachment.url,
                    file_name,
                    file_name.split(".")[-1],
                    thread_name,
                )

            # Give time for folder and things to be created and completed
            sleep(3)

    except Exception as e:
        logger.error(f"Failed to queue image download: {e}")


async def process_message(message, thread_name=None, folder_id=None) -> None:
    """Process the message and download images/videos."""
    if "no upload" not in message.content.lower() and message.attachments:
        logger.debug(f"Recieved attachments: {message.attachments}")
        if not thread_name:
            thread_name = message.channel.name
        logger.info(f"Recieved message in {thread_name}")

        EXECUTOR.submit(
            queue_file_downloads, thread_name, message.attachments, folder_id
        )

        # Add a reaction to the message
        if message.guild is not None:
            try:
                await message.add_reaction(
                    utils.get(message.guild.emojis, name="glump_photo")
                )
            except errors.HTTPException as e:
                logger.debug(f"Failed to add reaction: {e}")
                await message.add_reaction("👍")
        else:
            await message.add_reaction("👍")


@bot.event
async def on_ready() -> None:
    """Event triggered when the bot is ready."""
    await bot.tree.sync()

    global GUILD
    if GUILD_ID is None:
        logger.error("GUILD_ID is not set. Exiting.")
        exit(1)

    # Get the guild (server) where the bot is running
    GUILD = bot.get_guild(int(GUILD_ID))

    logger.debug(f"Guild: {GUILD}")

    if not GUILD:
        logger.error("Failed to find guild")
        exit(1)

    logger.info(f"Logged in as {bot.user}")


@bot.event
async def on_message(message: message.Message) -> None:
    """Event triggered when a message is sent in a channel."""
    if isinstance(message.channel, Thread) and CHANNEL_NAME == str(
        message.channel.parent
    ):
        logger.debug(f"Recieved message: {message.content}")
        await process_message(message)


# Define the slash command to read messages from a thread
@bot.tree.command(
    name="threadimages",
    description="Read all messages in a specific thread to upload photos",
)
@app_commands.describe(thread_id="The ID of the thread to read messages from")
async def read_thread(interaction: Interaction, thread_id: str) -> None:
    """Read all messages in a specific thread to upload photos."""
    try:
        # Defer the initial response, alter discord to show that
        # the bot is "thinking/processing"
        await interaction.response.defer(ephemeral=True)

        logger.info(f"Reading thread command called with ID: {thread_id}")

        # Fetch the thread using the provided thread ID
        thread = await bot.fetch_channel(int(thread_id))

        logger.debug(f"Thread: {thread}")

        # Check if the channel is a thread
        if isinstance(thread, Thread):
            await interaction.response.send_message(
                f"Reading messages in thread: {thread.name}",
                ephemeral=True,
            )

            # Read and display all messages in the thread
            async for message in thread.history(limit=None):
                logger.debug(f"Message: {message}")
                # Check if the bot has already reacted to the message
                if not any(reaction.me for reaction in message.reactions):
                    await process_message(message)
        else:
            # If the channel is not a thread, send an error message
            await interaction.followup.send(
                "The provided ID does not correspond to a thread.", ephemeral=True
            )
    except NotFound:
        # If the thread is not found, send an error message
        await interaction.followup.send(
            "Thread not found. Please check the thread ID.", ephemeral=True
        )
        logger.info(f"Thread not found {thread_id}")
    except Exception as e:
        logger.error(f"An error occurred: {e}")

        # If any other error occurs, send an error message
        if not interaction.response.is_done():
            await interaction.followup.send("An error occurred", ephemeral=True)


@bot.tree.command(
    name="messageimages", description="Upload all attachments of a specific message"
)
@app_commands.describe(
    message_id="The ID of the message to upload attachments from",
    folder_name="The Folder Name where the attachments will be uploaded",
)
async def read_message(
    interaction: Interaction, message_id: str, folder_name: str
) -> None:
    """Upload all attachments of a specific message."""
    try:
        # Defer the initial response, alter discord to show that
        # the bot is "thinking/processing"
        await interaction.response.defer(ephemeral=True)

        logger.info(f"Reading message command called with ID: {message_id}")

        if not GUILD:
            raise Exception("Guild not found")
        for channel in GUILD.text_channels:
            try:
                # Check if the bot has permission to read the channel
                message = await channel.fetch_message(int(message_id))
                logger.debug(f"Message found in channel {channel.name}: {message}")

                if not any(reaction.me for reaction in message.reactions):
                    await process_message(message, thread_name=folder_name)

                    # Respond to the interaction with a message
                    await interaction.followup.send(
                        f"Photo/Videos being uploaded to {folder_name}",
                        ephemeral=True,
                    )
                return
            except NotFound:
                logger.debug(f"Message not found in channel {channel.name}")
                continue  # Message not found in this channel
            except Forbidden:
                logger.debug(
                    f"Bot does not have permission to read channel {channel.name}"
                )
                continue  # Bot doesn't have permission to read this channel
            except ValueError as e:
                logger.info(f"Given message id was not an integer: {e}")

                # If the message ID is not an integer, send an error message
                await interaction.followup.send(
                    "Given ID was not an integer",
                    ephemeral=True,
                )
                return

        # If the message was not found in any channel, send an error message
        await interaction.followup.send(
            "Message could not be found by the bot, check that the bot has"
            "permission to view the channel the message is in",
            ephemeral=True,
        )
        logger.info("Message not found in any accessible channels.")
    except Exception as e:
        logger.error(f"An error occurred: {e}")

        # If any other error occurs, send an error message
        if not interaction.response.is_done():
            await interaction.followup.send(
                "An error occurred contact administrator", ephemeral=True
            )


@bot.tree.command(name="help", description="Help command to show available commands")
async def help_message(interaction: Interaction) -> None:
    await interaction.response.send_message(
        "Commands:\n"
        "/threadimages <thread_id> - Read all messages in a specific thread to "
        "upload photos that were not previously uploaded by this bot\n"
        "/messageimages <message_id> <folder_name> - Upload all attachments of "
        "a specific message\n"
        "/changefolder <folder_id> - Change the folder ID of the channel to "
        "upload images to\n"
        "/help - Show this help message",
        ephemeral=True,
    )


@bot.tree.command(
    name="changefolder",
    description="Change the folder ID of the channel to upload images to",
)
@app_commands.describe(folder_id="The folder ID to upload images to")
async def change_folder_command(interaction: Interaction, folder_id: str) -> None:
    """A command that only allows users with a specific role to perform an action."""
    try:
        # Check if the user is a guild member and has the required role
        if isinstance(interaction.user, Member):
            member = interaction.user
        elif interaction.guild is not None:
            member = await interaction.guild.fetch_member(interaction.user.id)
        else:
            await interaction.response.send_message(
                "This command can only be used within a server.",
                ephemeral=True,
            )
            logger.warning(
                f"User {interaction.user} attempted restricted action outside a guild"
            )
            return

        if hasattr(member, "roles"):
            if any(role.name == ROLE_NAME for role in member.roles):
                await interaction.response.defer(ephemeral=True)

                if not check_parent_folder_id(folder_id):
                    await interaction.followup.send(
                        "Invalid folder ID provided.",
                        ephemeral=True,
                    )
                    return
                # Update the parent_folder_file with the new folder_id
                async with aiofiles.open(parent_folder_file, "w") as f:
                    await f.write(folder_id)
                global PARENT_FOLDER_ID
                PARENT_FOLDER_ID = folder_id
                await interaction.followup.send(
                    "Folder ID updated successfully!",
                    ephemeral=True,
                )
                logger.info(f"Folder ID changes by {interaction.user}")
            else:
                await interaction.response.send_message(
                    "You do not have the required role to perform this action.",
                    ephemeral=True,
                )
        else:
            await interaction.response.send_message(
                "This command can only be used within a server.",
                ephemeral=True,
            )
            logger.warning(
                f"User {interaction.user} attempted restricted action outside a guild"
            )
    except Exception as e:
        logger.error(f"An error occurred in restricted_action: {e}")
        if not interaction.response.is_done():
            await interaction.followup.send(
                "An error occurred contact administrator", ephemeral=True
            )


if __name__ == "__main__":
    setup_logger(logger, getattr(INFO, LOG_LEVEL, INFO))

    # Authenticate Google Drive service
    SERVICE = authenticate_google_drive()

    if not SERVICE:
        logger.error("Failed to authenticate Google Drive service")
        exit(1)

    if DISCORD_TOKEN:
        # Start the bot
        bot.run(DISCORD_TOKEN)
    else:
        logger.error("DISCORD_TOKEN is not set. Exiting.")
        exit(1)
