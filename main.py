from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from json import load
from logging import INFO, Formatter, StreamHandler, getLogger
from os import unlink, getenv
from re import compile as re_compile
from tempfile import NamedTemporaryFile
from time import sleep
from typing import Any
from pymongo import MongoClient

from discord import (
    Forbidden,
    Intents,
    Interaction,
    NotFound,
    Thread,
    app_commands,
    message,
    utils,
)
from discord.ext import commands
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from PIL import Image
from psutil import virtual_memory
from pyheif import read as pyheif_read
from requests import get, head

intents = Intents.default()
intents.message_content = True
intents.guilds = True
intents.message_content = True


client = MongoClient(getenv("MONGODB_URI", "mongodb://localhost:27017"))
db = client["photo-bot"]
config_collection = db["config"]
CONFIG = config_collection.find()[0]
client.close()

SHARED_DRIVE_ID = CONFIG["SHARED_DRIVE_ID"]
FOLDER_ID = CONFIG["PARENT_FOLDER_ID"]
GUILD = CONFIG["GUILD_ID"]

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

# google service
SERVICE = None

# discord commands bot
bot = commands.Bot(command_prefix="!", intents=intents)

# Create a thread pool for downloading images
EXECUTOR = ThreadPoolExecutor(max_workers=1)

logger = getLogger("photo-bot")


def setup_logger(logger_setup, log_level=INFO):
    logger_setup.setLevel(log_level)

    getLogger("discord.http").setLevel(log_level)
    handler = StreamHandler()
    formatter = Formatter(
        f"\x1b[30;1m%(asctime)s\x1b[0m \x1b[34;1m%(levelname)-8s\x1b[0m \x1b[35m%(name)s\x1b[0m %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger_setup.addHandler(handler)


def get_file_size(url) -> int | None:
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


def is_memory_available(file_size) -> bool:
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
    delegated_creds = creds.with_subject(CONFIG["DELEGATE_EMAIL"])

    logger.info("creating google cloud service")
    service = build("drive", "v3", credentials=delegated_creds)
    return service


def check_folder_exists(folder_name) -> str | None:
    try:
        if not SERVICE:
            raise Exception("Google Drive service not authenticated")

        for _ in range(3):
            try:
                response = (
                    SERVICE.files()
                    .list(
                        q=f"'{FOLDER_ID}' in parents and name='{folder_name}'",  # Query to filter by folder parent
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
                sleep(1)

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
            "parents": [FOLDER_ID],  # Set the parent folder in the shared drive
        }

        for _ in range(3):
            # Create the new folder in the specified shared drive folder
            try:
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
                sleep(3)

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

        if stream_data:
            media = MediaIoBaseUpload(
                stream_data, mimetype=f"{file_type}/{extension}", resumable=True
            )
        elif file_path:
            media = MediaFileUpload(
                file_path, mimetype=f"{file_type}/{extension}", resumable=True
            )

        if media:
            for _ in range(3):
                try:
                    # Upload the file
                    uploaded_file = (
                        SERVICE.files()
                        .create(
                            body=file_metadata,
                            media_body=media,
                            supportsAllDrives=True,  # Ensures compatibility with shared drives
                            fields="id, name",
                        )
                        .execute()
                    )
                    break
                except Exception as e:
                    logger.debug(f"Failed to upload image: {e}")
                    sleep(3)
        else:
            logger.error("No media data to upload")
            return

        if uploaded_file:
            logger.info(
                f"Uploaded {file_name} to {thread_name}, File ID: {uploaded_file.get('id')}"
            )
        else:
            logger.warning(f"Failed to upload image: {file_name.upper()}")
        sleep(1)
    except Exception as e:
        logger.error(f"Failed to upload image: {e}")


def download_image(url, file_name, folder_id, extension, thread_name) -> None:
    try:
        logger.debug(f"Downloading image from {url}")
        for _ in range(3):
            try:
                response = get(url)
                logger.debug(f"Response {url}: {response.status_code}")

                if response.status_code == 200:
                    image_data = response.content

                    logger.debug(f"Downloaded image from {url}")

                    if "heic" == extension or "heif" == extension:
                        image_data, file_name, extension = convert_to_jpeg(
                            image_data, file_name, extension
                        )

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
                sleep(3)

        logger.error(f"Failed to download image: {url}")
    except Exception as e:
        logger.error(f"Failed to download image: {e}")


def download_video(folder_id, url, file_name, extension, thread_name) -> None:
    try:

        logger.debug(f"Downloading video from {url}")

        for _ in range(3):
            try:
                file_size = get_file_size(url)

                logger.debug(f"File size: {file_size}")

                response = get(url, stream=True)
                logger.debug(f"Response {url}: {response.status_code}")

                if response.status_code == 200:
                    if (
                        CONFIG["VIDEO_IN_MEMORY"]
                        and file_size
                        and is_memory_available(file_size)
                    ):

                        logger.debug(f"Downloading video from {url} to memory")

                        # Use BytesIO as an in-memory file to store the download stream
                        video_stream = BytesIO()

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
                sleep(3)
    except Exception as e:
        logger.error(f"Failed to download video: {e}")


def find_file_name(pattern, url) -> str | None:
    try:
        return pattern.findall(url)[0].replace(" ", "_").replace("'", "\x27")
    except Exception as e:
        logger.debug(f"Failed to find file name: {e}")
    return None


def queue_file_downloads(thread_name, attachments, folder_id=None) -> None:

    try:
        thread_name = thread_name.replace("'", "\x27")
        logger.debug(f"Thread Name: {thread_name}")

        if folder_id is None:
            folder_id = check_folder_exists(thread_name)
            if folder_id is None:
                folder_id = create_folder(thread_name)

        logger.info(f"FOLDER ID: {folder_id}")

        if not folder_id:
            logger.debug("Missing folder ID")
            return

        for attachment in attachments:
            url_lower = attachment.url.lower()

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


async def process_message(message, thread_name=None, folder_id=None):
    if "no upload" not in message.content.lower() and message.attachments:
        logger.debug(f"Recieved attachments: {message.attachments}")
        if not thread_name:
            thread_name = message.channel.name
        logger.info(f"Recieved message in {thread_name}")

        EXECUTOR.submit(
            queue_file_downloads, thread_name, message.attachments, folder_id
        )

        if message.guild is not None:
            emoji = utils.get(message.guild.emojis, name="glump_photo")
            if emoji:
                await message.add_reaction(emoji)
            else:
                await message.add_reaction("👍")
        else:
            await message.add_reaction("👍")


@bot.event
async def on_ready() -> None:
    await bot.tree.sync()

    global GUILD
    logger.debug(f"Guild: {GUILD}")
    if not GUILD:
        logger.error("Failed to find guild")
        exit(1)

    logger.info(f"Logged in as {bot.user}")


@bot.event
async def on_message(message: message.Message) -> None:

    if isinstance(message.channel, Thread) and CONFIG["CHANNEL_NAME"] == str(
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
    try:
        await interaction.response.defer(ephemeral=True)  # Defer the initial response
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
                await process_message(message)
        else:
            await interaction.followup.send(
                "The provided ID does not correspond to a thread.", ephemeral=True
            )
    except NotFound:
        await interaction.followup.send(
            "Thread not found. Please check the thread ID.", ephemeral=True
        )
        logger.info(f"Thread not found {thread_id}")
    except Exception as e:
        logger.error(f"An error occurred: {e}")
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
    try:
        await interaction.response.defer(ephemeral=True)  # Defer the initial response
        logger.info(f"Reading message command called with ID: {message_id}")
        if not GUILD:
            raise Exception("Guild not found")
        for channel in GUILD.text_channels:
            try:
                message = await channel.fetch_message(int(message_id))
                logger.debug(f"Message found in channel {channel.name}: {message}")
                await process_message(message, thread_name=folder_name)
                await interaction.followup.send(
                    f"Photo/Videos being uploaded to {folder_name}",
                    ephemeral=True,
                )
                return
            except NotFound:
                logger.debug(f"Message not found in channel {channel.name}")
                continue  # Message not found in this channel
            except Forbidden:
                logger.warning(
                    f"Bot does not have permission to read channel {channel.name}"
                )
                continue  # Bot doesn't have permission to read this channel
            except ValueError as e:
                logger.info(f"Given message id was not an integer: {e}")
                await interaction.followup.send(
                    f"Given ID was not an integer",
                    ephemeral=True,
                )
                return
        await interaction.followup.send(
            f"Message could not be found by the bot, check that the bot has permission to view the channel the message is in",
            ephemeral=True,
        )
        logger.info("Message not found in any accessible channels.")
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        if not interaction.response.is_done():
            await interaction.followup.send(
                "An error occurred contact administrator", ephemeral=True
            )


if __name__ == "__main__":
    setup_logger(logger, CONFIG.get("LOGGING", "INFO").upper())
    logger.debug(f"Loaded config: {CONFIG}")

    SERVICE = authenticate_google_drive()

    if not SERVICE:
        print("Failed to authenticate Google Drive service")
        exit(1)

    bot.run(CONFIG["DISCORD_TOKEN"])
