import json
import logging
import logging.handlers
import re
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import discord
import pyheif
import requests
from discord.ext import commands
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from PIL import Image

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.message_content = True

CONFIG = {}

SHARED_DRIVE_ID = ""
FOLDER_ID = ""

EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".heic",
    ".heif",
)
SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.file",
]
IMAGE_NAME_PATTERN = re.compile(r"([\w]+\.(?:png|jpg|jpeg|heic|heif))")

# google service
SERVICE = None

# discord commands bot
bot = commands.Bot(command_prefix="!", intents=intents)

# Create a thread pool for downloading images
EXECUTOR = ThreadPoolExecutor(max_workers=1)

logger = logging.getLogger("photo-bot")


def setup_logger(logger_setup, log_level=logging.INFO):
    logger_setup.setLevel(log_level)

    logging.getLogger("discord.http").setLevel(log_level)
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        f"\x1b[30;1m%(asctime)s\x1b[0m \x1b[34;1m%(levelname)-8s\x1b[0m \x1b[35m%(name)s\x1b[0m %(message)s",
        "%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    logger_setup.addHandler(handler)


def validate_config(config) -> None:
    """Validate the configuration file to ensure all required keys are present"""
    if any(
        key not in config
        for key in (
            "DISCORD_TOKEN",
            "PARENT_FOLDER_ID",
            "CHANNEL_NAME",
            "SHARED_DRIVE_ID",
        )
    ):
        logger.error("Missing required configuration keys")
        exit(1)


def authenticate_google_drive():
    """Authenticate the user and return a service object"""
    logger.info("authenticating google cloud service account")
    creds = Credentials.from_service_account_file(
        "config/service-credentials.json", scopes=SCOPES
    )
    delegated_creds = creds.with_subject("glump@apoez.org")

    logger.info("creating google cloud service")
    service = build("drive", "v3", credentials=delegated_creds)
    return service


def check_folder_exists(folder_name):
    try:
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
                time.sleep(1)

        folders = response.get("files", [])
        if folders:
            return folders[0].get("id")

        logger.debug(f"Failed to find folder: {folder_name}")
    except Exception as e:
        logger.error(f"Failed to find folder: {e}")
    return None


def create_folder(folder_name):

    try:

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
                time.sleep(3)

        if new_folder:
            return new_folder.get("id")

        logger.error(f"Failed to create folder: {folder_name}")
    except Exception as e:
        logger.error(f"Failed to create folder: {e}")
    return None


def upload_image(folder_id, image_data, image_name, extension, thread_name):

    try:
        # Define metadata for the new file
        file_metadata = {
            "name": image_name.upper(),
            "parents": [folder_id],  # Specify the parent folder ID
        }

        if extension == "jpg":
            extension = "jpeg"

        # Set up the media upload using the BytesIO object
        try:
            media = MediaIoBaseUpload(
                image_data, mimetype=f"image/{extension}", resumable=True
            )
        except Exception as e:
            logger.debug(f"Failed to create media object: {e}")
            return

        for _ in range(3):
            try:
                # Upload the file
                uploaded_image = (
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
                time.sleep(3)

        if uploaded_image:
            logger.info(
                f"Uploaded {image_name} to {thread_name}, File ID: {uploaded_image.get("id")}"
            )
        else:
            logger.warning(f"Failed to upload image: {image_name}")
        time.sleep(1)
    except Exception as e:
        logger.error(f"Failed to upload image: {e}")


def download_image(url, file_name, folder_id, extension, thread_name):
    try:
        logger.debug(f"Downloading image from {url}")
        for _ in range(3):
            try:
                response = requests.get(url)
                logger.debug(f"Response {url}: {response.status_code}")

                if response.status_code == 200:
                    image_data = response.content

                    logger.debug(f"Downloaded image from {url}")

                    if "heic" == extension or "heif" == extension:
                        try:
                            logger.debug("Converting HEIC/HEIF image")
                            heif_file = pyheif.read(BytesIO(response.content))

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
                            image_data = img_bytes.getvalue()
                            extension = "jpeg"
                            file_name = file_name.replace("heic", "jpeg")

                            logger.debug("Converted HEIC/HEIF image")
                        except Exception as e:
                            logger.debug(f"Failed to convert HEIC/HEIF image: {e}")
                            image_data = response.content

                    image_data_bytes = BytesIO(image_data)

                    upload_image(
                        folder_id, image_data_bytes, file_name, extension, thread_name
                    )
                    return
                else:
                    logger.debug(f"Failed to download image from {url}")
            except Exception as e:
                logger.debug(f"Failed to download image: {e}")
                time.sleep(3)

        logger.error(f"Failed to download image: {url}")
    except Exception as e:
        logger.error(f"Failed to download image: {e}")


def queue_image_download(thread_name, attachments, folder_id=None):

    try:
        thread_name = thread_name.replace("'", "\x27")
        logger.debug(f"Thread Name: {thread_name}")

        if folder_id is None:
            folder_id = check_folder_exists(thread_name)
            if folder_id is None:
                folder_id = create_folder(thread_name)

        logger.info(f"FOLDER ID: {folder_id}")

        if folder_id:
            for attachment in attachments:
                url_lower = attachment.url.lower()

                if any(ext in url_lower for ext in EXTENSIONS):

                    logger.debug(f"Found image attachment: {attachment.url}")

                    file_name = IMAGE_NAME_PATTERN.findall(url_lower)[0]

                    file_name = file_name.replace(" ", "_").replace("'", "\x27")

                    if file_name is None:
                        logger.debug("Could not find image name")
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
    except Exception as e:
        logger.error(f"Failed to queue image download: {e}")


async def process_message(message, folder_id=None):
    if "no upload" not in message.content.lower() and message.attachments:
        logger.debug(f"Recieved attachments: {message.attachments}")
        thread_name = message.channel.name
        logger.info(f"Recieved message in {thread_name}")

        EXECUTOR.submit(
            queue_image_download, thread_name, message.attachments, folder_id
        )

        if message.guild is not None:
            emoji = discord.utils.get(message.guild.emojis, name="glump_photo")
            if emoji:
                await message.add_reaction(emoji)
            else:
                await message.add_reaction("👍")
        else:
            await message.add_reaction("👍")


@bot.event
async def on_ready() -> None:
    logger.info(f"Logged in as {bot.user}")


@bot.event
async def on_message(message: discord.message.Message) -> None:
    logger.debug(f"Recieved message: {message.content}")

    if isinstance(message.channel, discord.Thread) and CONFIG["CHANNEL_NAME"] == str(
        message.channel.parent
    ):
        await process_message(message)


if __name__ == "__main__":
    with open("config/config.json", "r") as config_file:
        CONFIG = json.load(config_file)

    setup_logger(logger, CONFIG.get("LOGGING", "INFO").upper())
    logger.debug(f"Loaded config: {CONFIG}")

    validate_config(CONFIG)

    SHARED_DRIVE_ID = CONFIG["SHARED_DRIVE_ID"]
    FOLDER_ID = CONFIG["PARENT_FOLDER_ID"]

    SERVICE = authenticate_google_drive()

    if not SERVICE:
        print("Failed to authenticate Google Drive service")
        exit(1)

    bot.run(CONFIG["DISCORD_TOKEN"])
