import logging
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

# Program variables
DISCORD_TOKEN = None
CHANNEL_NAME = None
SHARED_DRIVE_ID = None
GUILD_ID = None
VIDEO_IN_MEMORY = False
DELEGATE_EMAIL = None
LOG_LEVEL = "INFO"
ROLE_NAME = None
PARENT_FOLDER_ID = None
# Exponential backoff delays: 2s, 6s, 18s, 54s, 120s (capped)
EXPONENTIAL_BACKOFF_DELAYS = [2.0, 6.0, 18.0, 54.0, 120.0]
parent_folder_file = "config/parent_folder_id.txt"

# Network bounds: connect and read timeouts (seconds) for every external request
REQUEST_TIMEOUT = (5, 30)
# Maximum accepted download size per file; overridable through MAX_FILE_SIZE_MB
MAX_FILE_SIZE_MB = 50
# Defense-in-depth allowlist for folder names derived from Discord threads
FOLDER_NAME_PATTERN = re_compile(r"[\w\- ]{1,100}")

# Setup Discord intents
intents = Intents.default()
intents.message_content = True
intents.guilds = True

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


def _public_url(url: str) -> str:
    """Strip signed query parameters so CDN bearer tokens never hit logs."""
    return url.split("?", 1)[0]


def _safe_log_value(value: Any) -> str:
    """Remove newlines from user-controlled text before logging it."""
    return str(value).replace("\r", "").replace("\n", "")


def _safe_exception(error: Exception) -> str:
    """Strip signed URL query strings and newlines from exception messages."""
    return _safe_log_value(_public_url(str(error)))


def escape_drive_query_value(value: str) -> str:
    r"""Escape a value for use inside a Google Drive ``q`` string literal.

    Drive's query syntax treats backslash as the escape character, so
    backslashes must be escaped before quotes.
    """
    return value.replace("\\", "\\\\").replace("'", "\\'")


def ensure_guild(interaction: Interaction) -> bool:
    """Return True only when the interaction comes from the configured guild."""
    guild = getattr(interaction, "guild", None)
    return guild is not None and str(guild.id) == str(GUILD_ID)


def get_file_size(url: str) -> int | None:
    """Returns the file size in bytes from a URL."""
    public_url = _public_url(url)
    try:
        response = head(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200 and "Content-Length" in response.headers:
            logger.debug(
                f"File size for {public_url}: "
                f"{response.headers['Content-Length']} bytes"
            )
            return int(response.headers["Content-Length"])
        else:
            logger.warning(
                f"Could not retrieve file size for {public_url} "
                f"(status {response.status_code}, no Content-Length)"
            )
            return None
    except Exception as e:
        logger.warning(
            f"Failed to get file size for {public_url}: {_safe_exception(e)}"
        )
        return None


def is_memory_available(file_size: int) -> bool:
    """Check if enough memory is available for the given file size."""
    available_memory = virtual_memory().available

    logger.debug(f"Available memory: {available_memory}")

    return (available_memory - 20000) > file_size


def _max_file_size_bytes() -> int:
    """Return the configured download size cap in bytes."""
    return MAX_FILE_SIZE_MB * 1024 * 1024


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
        logger.warning(
            f"Invalid folder ID provided: {_safe_log_value(folder_id)} - {e}"
        )
    return False


def check_folder_exists(folder_name: str) -> str | None:
    safe_folder_name = _safe_log_value(folder_name)
    escaped_folder_name = escape_drive_query_value(folder_name)
    try:
        if not SERVICE:
            raise Exception("Google Drive service not authenticated")

        logger.debug(f"Searching for folder: {safe_folder_name}")

        response = None
        for attempt in range(5):
            try:
                # Search for the folder in the specified shared drive
                # using the folder name and parent folder ID
                response = (
                    SERVICE.files()
                    .list(
                        # Query to filter by folder parent ID and name
                        q=(
                            f"'{PARENT_FOLDER_ID}' in parents and "
                            f"name='{escaped_folder_name}'"
                        ),
                        corpora="drive",
                        driveId=SHARED_DRIVE_ID,
                        includeItemsFromAllDrives=True,
                        supportsAllDrives=True,
                    )
                    .execute()
                )
                break
            except Exception as e:
                logger.warning(
                    f"Failed to find folder '{safe_folder_name}' "
                    f"(attempt {attempt + 1}/5): {e}"
                )
                if attempt < 4:  # Don't sleep after last attempt
                    sleep(EXPONENTIAL_BACKOFF_DELAYS[attempt])

        # Check if the folder exists in the response
        if response is None:
            logger.error(
                f"Failed to find folder '{safe_folder_name}' in parent "
                f"{PARENT_FOLDER_ID} after retries"
            )
            return None
        folders = response.get("files", [])
        if folders:
            return folders[0].get("id")

        logger.info(f"Folder '{safe_folder_name}' does not exist yet")
    except Exception as e:
        logger.error(f"Failed to find folder '{safe_folder_name}': {e}")
    return None


def create_folder(folder_name) -> str | None:
    safe_folder_name = _safe_log_value(folder_name)
    try:
        if not SERVICE:
            raise Exception("Google Drive service not authenticated")

        # Define the metadata for the new folder
        folder_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [PARENT_FOLDER_ID],  # Set the parent folder in the shared drive
        }

        logger.debug(f"Creating folder: {safe_folder_name}")

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
                logger.warning(
                    f"Failed to create folder '{safe_folder_name}' "
                    f"(attempt {attempt + 1}/5): {e}"
                )
                if attempt < 4:  # Don't sleep after last attempt
                    sleep(EXPONENTIAL_BACKOFF_DELAYS[attempt])

        if new_folder:
            logger.info(
                f"Created folder '{safe_folder_name}' ({new_folder.get('id')}) "
                f"in parent {PARENT_FOLDER_ID}"
            )
            return new_folder.get("id")

        logger.error(
            f"Failed to create folder '{safe_folder_name}' after retries "
            f"in parent {PARENT_FOLDER_ID}"
        )
    except Exception as e:
        logger.error(f"Failed to create folder '{safe_folder_name}': {e}")
    return None


def convert_to_jpeg(image_data, file_name, extension):
    safe_file_name = _safe_log_value(file_name)
    try:
        logger.debug(f"Converting HEIC/HEIF image {safe_file_name} ({extension})")
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

        logger.debug(f"Converted HEIC/HEIF image {safe_file_name} to {new_file_name}")

        return new_image_data, new_file_name, new_extension
    except Exception as e:
        logger.warning(
            f"Failed to convert HEIC/HEIF image {safe_file_name}, "
            f"uploading original: {e}"
        )
        return image_data, file_name, extension


def upload(
    folder_id, stream_data, file_name, extension, thread_name, file_type, file_path=None
) -> None:
    safe_file_name = _safe_log_value(file_name)
    safe_thread_name = _safe_log_value(thread_name)
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
                    logger.warning(
                        f"Failed to upload {safe_file_name} to folder {folder_id} "
                        f"(thread '{safe_thread_name}', attempt "
                        f"{attempt + 1}/5): {e}"
                    )
                    if attempt < 4:  # Don't sleep after last attempt
                        sleep(EXPONENTIAL_BACKOFF_DELAYS[attempt])

            # Check if the upload was successful
            if uploaded_file:
                logger.info(
                    f"Uploaded {safe_file_name} ({file_type}) to thread "
                    f"'{safe_thread_name}' in folder {folder_id}, "
                    f"File ID: {uploaded_file.get('id')}"
                )
            else:
                logger.error(
                    f"Failed to upload {safe_file_name} ({file_type}) to thread "
                    f"'{safe_thread_name}' in folder {folder_id} after retries"
                )
        else:
            logger.error(
                f"No media data to upload for {safe_file_name} to thread "
                f"'{safe_thread_name}' in folder {folder_id}"
            )
        sleep(1)
    except Exception as e:
        logger.error(
            f"Failed to queue downloads for thread '{safe_thread_name}': "
            f"{_safe_exception(e)}"
        )


def download_image(url, file_name, folder_id, extension, thread_name, retry) -> None:
    public_url = _public_url(url)
    safe_file_name = _safe_log_value(file_name)
    safe_thread_name = _safe_log_value(thread_name)
    try:
        logger.debug(
            f"Downloading image {safe_file_name} from {public_url} to folder "
            f"{folder_id} (thread '{safe_thread_name}')"
        )
        for attempt in range(5):
            try:
                # Request the image data
                response = get(url, timeout=REQUEST_TIMEOUT)
                logger.debug(f"Response {public_url}: {response.status_code}")

                if response.status_code == 200:
                    # Get the image data
                    image_data = response.content

                    logger.debug(f"Downloaded image {safe_file_name} from {public_url}")

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
                    logger.warning(
                        f"Failed to download image {safe_file_name} from "
                        f"{public_url}: status {response.status_code} "
                        f"(attempt {attempt + 1}/5)"
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to download image {safe_file_name} from {public_url} "
                    f"(attempt {attempt + 1}/5): {_safe_exception(e)}"
                )
                if attempt < 4:  # Don't sleep after last attempt
                    sleep(EXPONENTIAL_BACKOFF_DELAYS[attempt])

        if not retry:
            # Readd current download to queue for 1 more additional attempt to download
            # this image
            EXECUTOR.submit(
                download_image,
                url,
                file_name,
                folder_id,
                extension,
                thread_name,
                True,
            )
            logger.error(
                f"Failed to download image {safe_file_name} from {public_url} to "
                f"folder {folder_id} after 5 attempts, readding the download to "
                "the queue"
            )
            return

        logger.error(
            f"Failed to download image {safe_file_name} from {public_url} to folder "
            f"{folder_id} after 5 attempts, and a retry of the function"
        )
    except Exception as e:
        logger.error(
            f"Failed to download image {safe_file_name} from {public_url} to folder "
            f"{folder_id}: {_safe_exception(e)}"
        )


def download_video(folder_id, url, file_name, extension, thread_name, retry) -> None:
    public_url = _public_url(url)
    safe_file_name = _safe_log_value(file_name)
    safe_thread_name = _safe_log_value(thread_name)
    max_size = _max_file_size_bytes()
    try:
        logger.debug(
            f"Downloading video {safe_file_name} from {public_url} to folder "
            f"{folder_id} (thread '{safe_thread_name}')"
        )

        for attempt in range(5):
            try:
                # Get the file size from the URL
                file_size = get_file_size(url)

                logger.debug(f"File size for {public_url}: {file_size} bytes")

                if file_size and file_size > max_size:
                    logger.warning(
                        f"Skipping video {safe_file_name} from {public_url}: "
                        f"{file_size} bytes exceeds the {MAX_FILE_SIZE_MB} MB limit"
                    )
                    return

                # Request the video data
                response = get(url, stream=True, timeout=REQUEST_TIMEOUT)
                logger.debug(f"Response {public_url}: {response.status_code}")

                if response.status_code == 200:
                    response_size = response.headers.get("Content-Length")
                    if response_size is not None:
                        try:
                            if int(response_size) > max_size:
                                logger.warning(
                                    f"Skipping video {safe_file_name} from "
                                    f"{public_url}: {response_size} bytes exceeds "
                                    f"the {MAX_FILE_SIZE_MB} MB limit"
                                )
                                return
                        except (TypeError, ValueError):
                            pass

                    logger.debug(f"Downloaded video {safe_file_name} from {public_url}")

                    # Check if the file size is available and if memory is available
                    if VIDEO_IN_MEMORY and file_size and is_memory_available(file_size):
                        logger.debug(
                            f"Downloading video {safe_file_name} from {public_url} "
                            "to memory"
                        )

                        # Use BytesIO as an in-memory file to store the download stream
                        video_stream = BytesIO()
                        downloaded = 0

                        # Write the video content to the stream in chunks
                        for chunk in response.iter_content(chunk_size=8192):
                            downloaded += len(chunk)
                            if downloaded > max_size:
                                logger.warning(
                                    f"Aborting video {safe_file_name} from "
                                    f"{public_url}: exceeded the "
                                    f"{MAX_FILE_SIZE_MB} MB limit mid-stream"
                                )
                                return
                            video_stream.write(chunk)

                        # Reset the stream position to the start
                        video_stream.seek(0)

                        logger.debug(
                            f"Completed download of {safe_file_name} to memory"
                        )

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
                        logger.debug(
                            f"Downloading video {safe_file_name} from {public_url} "
                            "to disk"
                        )

                        # Create a temporary file with 'wb+' mode to read/write binary
                        temp_file = NamedTemporaryFile(
                            delete=False, suffix=f".{extension}"
                        )

                        downloaded = 0

                        # Write the video content to the temp file in chunks
                        for chunk in response.iter_content(chunk_size=8192):
                            downloaded += len(chunk)
                            if downloaded > max_size:
                                logger.warning(
                                    f"Aborting video {safe_file_name} from "
                                    f"{public_url}: exceeded the "
                                    f"{MAX_FILE_SIZE_MB} MB limit mid-stream"
                                )
                                temp_file.close()
                                unlink(temp_file.name)
                                return
                            temp_file.write(chunk)

                        temp_file.flush()  # Ensure all data is written
                        temp_file.seek(
                            0
                        )  # Move to the beginning of the file for reading

                        logger.debug(
                            f"Completed download of {safe_file_name} to disk: "
                            f"{temp_file.name}"
                        )

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
                            logger.error(
                                f"Failed to download video {safe_file_name} from "
                                f"{public_url} to disk"
                            )
                            return
                else:
                    logger.warning(
                        f"Failed to download video {safe_file_name} from "
                        f"{public_url}: status {response.status_code} "
                        f"(attempt {attempt + 1}/5)"
                    )
            except Exception as e:
                logger.warning(
                    f"Failed to download video {safe_file_name} from {public_url} "
                    f"(attempt {attempt + 1}/5): {_safe_exception(e)}"
                )
                if attempt < 4:  # Don't sleep after last attempt
                    sleep(EXPONENTIAL_BACKOFF_DELAYS[attempt])

        if not retry:
            # Readd current download to queue for 1 more additional attempt to download
            # this video
            EXECUTOR.submit(
                download_video,
                folder_id,
                url,
                file_name,
                extension,
                thread_name,
                True,
            )
            logger.error(
                f"Failed to download video {safe_file_name} from {public_url} to "
                f"folder {folder_id} after 5 attempts, readding the download to "
                "the queue"
            )
            return

        logger.error(
            f"Failed to download video {safe_file_name} from {public_url} to folder "
            f"{folder_id} after 5 attempts, and a retry of the function"
        )
    except Exception as e:
        logger.error(
            f"Failed to download video {safe_file_name} from {public_url} to folder "
            f"{folder_id}: {_safe_exception(e)}"
        )


def find_file_name(pattern, url) -> str | None:
    """Find the file name from the URL using regex pattern."""
    try:
        return pattern.findall(url)[0].replace(" ", "_")
    except Exception as e:
        logger.warning(f"Failed to find file name from {_public_url(url)}: {e}")
    return None


def queue_file_downloads(thread_name, attachments, folder_id=None) -> None:
    """Queue the file downloads for images and videos."""
    safe_thread_name = _safe_log_value(thread_name)
    try:
        if not FOLDER_NAME_PATTERN.fullmatch(thread_name):
            logger.warning(
                f"Rejecting unsafe folder name '{safe_thread_name}', skipping downloads"
            )
            return

        logger.debug(
            f"Queueing {len(attachments)} attachment(s) for thread '{safe_thread_name}'"
        )

        # Check if the folder ID is provided, if not, check if it exists
        if folder_id is None:
            folder_id = check_folder_exists(thread_name)
            if folder_id is None:
                folder_id = create_folder(thread_name)

        logger.debug(f"Using folder {folder_id} for thread '{safe_thread_name}'")

        if not folder_id:
            logger.warning(
                f"No folder ID available for thread '{safe_thread_name}', "
                "skipping downloads"
            )
            return

        max_size = _max_file_size_bytes()

        # Iterate through the attachments
        for attachment in attachments:
            url_lower = attachment.url.lower()
            public_url = _public_url(attachment.url)

            logger.debug(f"Attachment URL: {public_url}")

            attachment_size = getattr(attachment, "size", None)
            if attachment_size is not None and attachment_size > max_size:
                logger.warning(
                    f"Skipping attachment {public_url}: {attachment_size} bytes "
                    f"exceeds the {MAX_FILE_SIZE_MB} MB limit"
                )
                continue

            # Check if the URL contains image or video extensions
            if any(ext in url_lower for ext in IMAGE_EXTENSIONS):
                logger.debug(f"Found image attachment: {public_url}")

                file_name = find_file_name(IMAGE_NAME_PATTERN, url_lower)

                if file_name is None:
                    logger.warning(f"Could not find image file name in {public_url}")
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
                    False,
                )

            # Check if the URL contains video extensions
            elif any(ext in url_lower for ext in VIDEO_EXTENSIONS):
                logger.debug(f"Found video attachment: {public_url}")

                file_name = find_file_name(VIDEO_NAME_PATTERN, url_lower)

                if file_name is None:
                    logger.warning(f"Could not find video file name in {public_url}")
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
                    False,
                )

            # Give time for folder and things to be created and completed
            sleep(3)

    except Exception as e:
        logger.error(
            f"Failed to queue downloads for thread '{safe_thread_name}': "
            f"{_safe_exception(e)}"
        )


async def process_message(message, thread_name=None, folder_id=None) -> None:
    """Process the message and download images/videos."""
    if "no upload" not in message.content.lower() and message.attachments:
        logger.debug(
            f"Received {len(message.attachments)} attachment(s) for message "
            f"{message.id}"
        )
        if not thread_name:
            thread_name = message.channel.name
        logger.info(
            f"Processing message {message.id} with "
            f"{len(message.attachments)} attachment(s) in thread "
            f"'{_safe_log_value(thread_name)}'"
        )

        EXECUTOR.submit(
            queue_file_downloads, thread_name, message.attachments, folder_id
        )

        # Add a reaction to the message
        if message.guild is not None:
            try:
                emoji = utils.get(message.guild.emojis, name="glump_photo")
                if emoji is not None:
                    await message.add_reaction(emoji)
                else:
                    await message.add_reaction("👍")
            except errors.HTTPException as e:
                logger.debug(f"Failed to add reaction: {e}")
                await message.add_reaction("👍")
        else:
            await message.add_reaction("👍")


@bot.event
async def on_ready() -> None:
    """Event triggered when the bot is ready."""
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

    try:
        # Commands are registered before config is loaded, so scope them to the
        # configured guild here and remove any global registration that would
        # expose them outside this server.
        if bot.tree.get_commands():
            bot.tree.copy_global_to(guild=GUILD)
        bot.tree.clear_commands(guild=None)
        await bot.tree.sync()
        await bot.tree.sync(guild=GUILD)
    except errors.HTTPException as e:
        logger.error(f"Failed to start photo bot due to discord http exception: {e}")
    except Exception as e:
        logger.error(f"Failed to start photo bot with exception: {e}")

    logger.info(f"Logged in as {bot.user}")


@bot.event
async def on_message(message: message.Message) -> None:
    """Event triggered when a message is sent in a channel."""
    if isinstance(message.channel, Thread) and CHANNEL_NAME == str(
        message.channel.parent
    ):
        logger.debug(f"Received message: {_safe_log_value(message.content)}")
        await process_message(message)


# Define the slash command to read messages from a thread
@bot.tree.command(
    name="threadimages",
    description="Read all messages in a specific thread to upload photos",
)
@app_commands.describe(thread_id="The ID of the thread to read messages from")
async def read_thread(interaction: Interaction, thread_id: str) -> None:
    """Read all messages in a specific thread to upload photos."""
    if not ensure_guild(interaction):
        await interaction.response.send_message(
            "This command can only be used in the configured server.",
            ephemeral=True,
        )
        return

    safe_thread_id = _safe_log_value(thread_id)
    try:
        # Defer the initial response, alter discord to show that
        # the bot is "thinking/processing"
        await interaction.response.defer(ephemeral=True)

        logger.info(
            f"User {interaction.user} invoked /threadimages for thread {safe_thread_id}"
        )

        # Fetch the thread using the provided thread ID
        thread = await bot.fetch_channel(int(thread_id))

        logger.debug(f"Thread: {thread}")

        # Check if the channel is a thread
        if isinstance(thread, Thread):
            await interaction.followup.send(
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
            logger.warning(
                f"Channel {safe_thread_id} is not a thread "
                f"(requested by {interaction.user})"
            )
            await interaction.followup.send(
                "The provided ID does not correspond to a thread.", ephemeral=True
            )
    except NotFound:
        # If the thread is not found, send an error message
        await interaction.followup.send(
            "Thread not found. Please check the thread ID.", ephemeral=True
        )
        logger.warning(
            f"Thread {safe_thread_id} not found (requested by {interaction.user})"
        )
    except Exception as e:
        logger.error(
            f"/threadimages failed for thread {safe_thread_id} "
            f"(requested by {interaction.user}): {e}"
        )

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
    if not ensure_guild(interaction):
        await interaction.response.send_message(
            "This command can only be used in the configured server.",
            ephemeral=True,
        )
        return

    safe_message_id = _safe_log_value(message_id)
    safe_folder_name = _safe_log_value(folder_name)
    try:
        # Defer the initial response, alter discord to show that
        # the bot is "thinking/processing"
        await interaction.response.defer(ephemeral=True)

        logger.info(
            f"User {interaction.user} invoked /messageimages for message "
            f"{safe_message_id} into folder '{safe_folder_name}'"
        )

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
                logger.warning(
                    f"Given message id is not an integer: {safe_message_id} "
                    f"(requested by {interaction.user}): {e}"
                )

                # If the message ID is not an integer, send an error message
                await interaction.followup.send(
                    "Given ID was not an integer",
                    ephemeral=True,
                )
                return

        # If the message was not found in any channel, send an error message
        await interaction.followup.send(
            "Message could not be found by the bot, check that the bot has "
            "permission to view the channel the message is in",
            ephemeral=True,
        )
        logger.warning(
            f"Message {safe_message_id} not found in any accessible channels "
            f"(requested by {interaction.user})"
        )
    except Exception as e:
        logger.error(
            f"/messageimages failed for message {safe_message_id} into folder "
            f"'{safe_folder_name}' (requested by {interaction.user}): {e}"
        )

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
    if not ensure_guild(interaction):
        await interaction.response.send_message(
            "This command can only be used in the configured server.",
            ephemeral=True,
        )
        return

    safe_folder_id = _safe_log_value(folder_id)
    try:
        # Check if the user is a guild member and has the required role
        if isinstance(interaction.user, Member):
            member = interaction.user
        elif interaction.guild is not None:
            member = await interaction.guild.fetch_member(interaction.user.id)
        else:
            logger.warning(
                f"User {interaction.user} attempted /changefolder to "
                f"{safe_folder_id} outside a guild"
            )
            await interaction.response.send_message(
                "This command can only be used within a server.",
                ephemeral=True,
            )
            return

        if hasattr(member, "roles"):
            if any(role.name == ROLE_NAME for role in member.roles):
                await interaction.response.defer(ephemeral=True)

                if not check_parent_folder_id(folder_id):
                    logger.warning(
                        f"User {interaction.user} provided invalid folder ID "
                        f"{safe_folder_id} to /changefolder"
                    )
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
                logger.info(
                    f"Folder ID changed to {safe_folder_id} by {interaction.user}"
                )
            else:
                logger.warning(
                    f"User {interaction.user} without role '{ROLE_NAME}' "
                    f"attempted /changefolder to {safe_folder_id}"
                )
                await interaction.response.send_message(
                    "You do not have the required role to perform this action.",
                    ephemeral=True,
                )
        else:
            logger.warning(
                f"User {interaction.user} attempted /changefolder to "
                f"{safe_folder_id} outside a guild"
            )
            await interaction.response.send_message(
                "This command can only be used within a server.",
                ephemeral=True,
            )
    except Exception as e:
        logger.error(
            f"/changefolder failed for folder {safe_folder_id} "
            f"(requested by {interaction.user}): {e}"
        )
        if not interaction.response.is_done():
            await interaction.followup.send(
                "An error occurred contact administrator", ephemeral=True
            )


def load_config() -> None:
    """Load env vars and required config, raising ValueError on missing vars."""
    global DISCORD_TOKEN, CHANNEL_NAME, SHARED_DRIVE_ID, GUILD_ID
    global VIDEO_IN_MEMORY, DELEGATE_EMAIL, LOG_LEVEL, ROLE_NAME
    global PARENT_FOLDER_ID, MAX_FILE_SIZE_MB

    # Load environment variables from .env file
    load_dotenv()

    DISCORD_TOKEN = getenv("DISCORD_TOKEN")
    CHANNEL_NAME = getenv("CHANNEL_NAME")
    SHARED_DRIVE_ID = getenv("SHARED_DRIVE_ID")
    GUILD_ID = getenv("GUILD_ID")
    VIDEO_IN_MEMORY = getenv("VIDEO_IN_MEMORY", "False").lower() == "true"
    DELEGATE_EMAIL = getenv("DELEGATE_EMAIL")
    LOG_LEVEL = getenv("LOG_LEVEL", "INFO").upper()
    ROLE_NAME = getenv("ROLE_NAME")

    try:
        MAX_FILE_SIZE_MB = int(getenv("MAX_FILE_SIZE_MB", "50"))
    except ValueError:
        MAX_FILE_SIZE_MB = 50

    if os.path.exists(parent_folder_file):
        print("Reading parent folder ID from file")
        with open(parent_folder_file, "r") as f:
            PARENT_FOLDER_ID = f.read().strip()
    else:
        print("No parent folder ID file found, using environment variable")
        PARENT_FOLDER_ID = getenv("PARENT_FOLDER_ID")

    # Raise if any critical variables are None
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
    if missing_vars:
        raise ValueError("Missing environment variables: " + ", ".join(missing_vars))


def init_sentry() -> None:
    """Setup Sentry only when a DSN is configured."""
    dsn = getenv("SENTRY_DSN")
    if not dsn:
        return

    sentry_sdk.init(
        dsn=dsn,
        integrations=[
            AioHttpIntegration(),  # For async HTTP requests
            StdlibIntegration(),  # For threading-related error tracking
        ],
        traces_sample_rate=float(getenv("SENTRY_TRACE_RATE", 0.2)),
    )


def main() -> None:
    global SERVICE

    try:
        load_config()
    except ValueError as e:
        print(e)
        exit(1)

    # Setup Sentry
    init_sentry()

    setup_logger(logger, getattr(logging, LOG_LEVEL, logging.INFO))

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


if __name__ == "__main__":
    main()
