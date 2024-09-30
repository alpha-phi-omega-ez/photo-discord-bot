import os
import re
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import discord
import pyheif
import requests
from discord.ext import commands
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from PIL import Image

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.message_content = True

TOKEN = os.environ.get("DISCORD_BOT_TOKEN")

EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".heic",
    ".heif",
)


SCOPES = ["https://www.googleapis.com/auth/drive"]

IMAGE_NAME_PATTERN = re.compile(r"([\w]+\.(?:png|jpg|jpeg|heic|heif))")
EXTENSION_PATTERN = re.compile(r"\.(?:png|jpg|jpeg|heic|heif)")

service = None

bot = commands.Bot(command_prefix="!", intents=intents)

# Create a thread pool for downloading images
executor = ThreadPoolExecutor(max_workers=5)

# If modifying these SCOPES, delete the file token.json.


def authenticate_google_drive():
    """Authenticate the user and return a service object."""
    creds = None
    # The file token.json stores the user's access and refresh tokens, and is created automatically
    # when the authorization flow completes for the first time.
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        print("Missing google service credentials")
        exit(1)
    # If there are no valid credentials, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    service = build("drive", "v3", credentials=creds)
    return service


def check_folder_exists(service, folder_name, parent_folder_id=None):
    """Checks if a folder exists in the Google Drive, optionally within a specified parent folder.

    Args:
        service: The authenticated Google Drive API service.
        folder_name: The name of the folder to check.
        parent_folder_id: The ID of the parent folder (optional).

    Returns:
        The folder ID if the folder exists, otherwise None.
    """

    query = f"mimeType='application/vnd.google-apps.folder' and name='{folder_name}'"
    if parent_folder_id:
        query += f" and parents='{parent_folder_id}'"

    results = service.files().list(q=query).execute()
    items = results.get("files", [])

    if items:
        return items[0]["id"]
    else:
        return None


def create_folder(service, folder_name, parent_folder_id=None):
    """Creates a new folder in the Google Drive, optionally within a specified parent folder.

    Args:
        service: The authenticated Google Drive API service.
        folder_name: The name of the new folder.
        parent_folder_id: The ID of the parent folder (optional).

    Returns:
        The ID of the newly created folder.
    """

    file_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }

    if parent_folder_id:
        file_metadata["parents"] = [parent_folder_id]

    folder = service.files().create(body=file_metadata, fields="id").execute()

    return folder.get("id")


def upload_image(service, folder_id, image_data, image_name, extension):
    """Uploads an image directly from the response content to a specified folder in Google Drive.

    Args:
        service: The authenticated Google Drive API service.
        folder_id: The ID of the folder where the image will be uploaded.
        image_data: The image data as a bytes object.
        image_name: The desired name for the uploaded image.
    """

    file_metadata = {"name": image_name, "parents": [folder_id]}

    if extension == "jpg":
        extension = "jpeg"

    media = {"mimeType": f"image/{extension}", "body": image_data}

    file = (
        service.files()
        .create(body=file_metadata, media_body=media, fields="id")
        .execute()
    )
    print("File ID: {}".format(file.get("id")))


def download_image(url, file_name, service, folder_id, extension):
    response = requests.get(url)
    if response.status_code == 200:
        image_data = response.content
        if "heic" == extension or "heif" == extension:
            try:
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
            except Exception as e:
                print(f"Failed to convert HEIC/HEIF image: {e}")
                image_data = response.content

        upload_image(service, folder_id, image_data, file_name, extension)
        print("done")
    else:
        print(f"Failed to download image from {url}")


@bot.event
async def on_ready() -> None:
    print(f"Logged in as {bot.user}")


@bot.event
async def on_message(message: discord.message.Message) -> None:
    print(message)

    if (
        isinstance(message.channel, discord.Thread)
        and str(message.channel.parent) == "brother-photos"
    ):
        print("Message is in a thread in brothers photos")
        print(message.channel.name)
        if message.attachments:  # Check if the message has attachments
            for attachment in message.attachments:
                print("attachment")
                print(attachment.url)
                url_lower = attachment.url.lower()
                if any(ext in url_lower for ext in EXTENSIONS):
                    # Access the name of the thread
                    thread_name = message.channel.name
                    file_name = IMAGE_NAME_PATTERN.findall(url_lower)[0].upper()
                    print(f"Thread Name: {thread_name}")
                    print(f"Image URL: {attachment.url}")
                    print(file_name)

                    if file_name is None:
                        print("Could not find image name")
                        continue

                    folder_id = check_folder_exists(service, thread_name, "parent_id")
                    if folder_id is None:
                        create_folder(service, thread_name, "parent_id")

                    # Download the image
                    executor.submit(
                        download_image,
                        attachment.url,
                        file_name,
                        service,
                        folder_id,
                        EXTENSION_PATTERN.findall(file_name)[0],
                    )


if __name__ == "__main__":
    if TOKEN is None:
        print("Please set DISCORD_BOT_TOKEN environment variables.")
        exit(1)

    # Ensure the downloads folder exists
    if not os.path.exists("downloads"):
        os.makedirs("downloads")

    service = authenticate_google_drive()

    bot.run(TOKEN)
