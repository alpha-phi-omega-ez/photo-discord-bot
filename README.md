# Photo Discord Bot

This bot reads in all the photos upload to threads in a discord channel and uploads them to google drive folders named after the thread.

#### Requirements

Install the [uv package manager](https://docs.astral.sh/uv/getting-started/installation/)

Install the required packages, uv will sync the packages from uv.lock

```
uv sync
```

## Running the program

The project requires some environment variables to be created, you can set them in an .env file or by exporting the variables.

### Environment Variables

| Variable Name          | Default Value | Description                                                   |
|------------------------|---------------|---------------------------------------------------------------|
| `DISCORD_TOKEN` | None | Discord bot token |
| `GUILD_ID` | None | Discord server id |
| `CHANNEL_NAME`| None | Discord channel name that the bot should listen to events |
| `SHARED_DRIVE_ID` | None | Shared Drive ID for the shared drive where images/videos will be uploaded |
| `PARENT_FOLDER_ID` | None | Folder Drive ID for the folder where images/videos will be uploaded |
| `LOG_LEVEL` | `INFO` | Set the log level for the application, defaults to INFO |
| `VIDEO_IN_MEMORY` | false | Boolean to enable the downloading and storing of videos in memory for quicker IO operations | 
| `DELEGATE_EMAIL` | None | The program runs on behalf of an email, so pass in an email that has access to the shared drive/folders where images/videos will be uploaded | 
| `SENTRY_DSN` | None | Set the sentry DSN | 
| `SENTRY_TRACE_RATE` | `1.0` | Set the sentry trace rate | 

uv installs packages in a virtual environment and handles everything so you just need to use uv to run the python files.

```
uv run main.py
```

## Linting

This project uses [ruff](https://docs.astral.sh/ruff/) on pull requests to ensure code is up to standard. You can run it locally with:

```bash
ruff check
```

It can also help resolve some issues through

```bash
ruff check --fix
```

and
```bash
ruff format
```

## Deployment

This project is currently deployed with Docker. On commit to the main branch GitHub actions builds and pushes new versions of the Docker image to the [photo-discord-bot packages](https://github.com/alpha-phi-omega-ez/photo-discord-bot/pkgs/container/photo-discord-bot)

## Authors

* [**Rafael Cenzano**](https://github.com/RafaelCenzano)

## License

This project is licensed under the AGPL License - see the [LICENSE](LICENSE) file for details

