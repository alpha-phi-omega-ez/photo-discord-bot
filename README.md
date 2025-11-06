# Photo Discord Bot

This bot reads in all the photos upload to threads in a discord channel and uploads them to google drive folders named after the thread.

#### Requirements

Install the [uv package manager](https://docs.astral.sh/uv/getting-started/installation/)

Install the required packages, uv will sync the packages from uv.lock

This project uses [Just](https://github.com/casey/just) for easy aliases for commands, if you install it you can use the just commands listed in this repo.

This will install pytest and coverage
```bash
just install
```
```bash
uv sync --extra dev
```

This will exclude testing frameworks, this is used for production
```bash
just install-dev
```
```bash
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
| `VIDEO_IN_MEMORY` | `false` | Boolean to enable the downloading and storing of videos in memory for quicker IO operations |
| `DELEGATE_EMAIL` | None | The program runs on behalf of an email, so pass in an email that has access to the shared drive/folders where images/videos will be uploaded |
| `ROLE_NAME` | None | Discord role required to use admin commands such as `change_folder` |
| `SENTRY_DSN` | None | Set the sentry DSN |
| `SENTRY_TRACE_RATE` | `1.0` | Set the sentry trace rate |
| `MAX_FILE_SIZE_MB` | `0` | Maximum allowed file size for downloads in megabytes (0 disables the limit) |
| `MEMORY_RESERVE_PERCENT` | `10.0` | Percentage of system memory to keep free before downloading files |
| `THREAD_POOL_WORKERS` | `4` | Number of worker threads used for background download tasks |
| `MAX_RETRIES` | `3` | Number of retry attempts for transient failures |
| `RETRY_BACKOFF_MULTIPLIER` | `2.5` | Exponential backoff multiplier applied between retries |

uv installs packages in a virtual environment and handles everything so you just need to use uv to run the python files.

```bash
just run
```
```bash
uv run main.py
```

## Testing

Testing uses pytest, there are currently unit tests and integration tests. The deprecation warning is ignored due to it being a warning in discord.py's code and is unrelated to this project as it doesn't use voice functionality in the `discord.player`.

Run the full test suite (unit + integration):

```bash
just test
```
```bash
uv run pytest -v -W ignore::DeprecationWarning:discord.player
```

Run only the unit tests:

```bash
just unit-test
```
```bash
uv run pytest tests/unit -v -W ignore::DeprecationWarning:discord.player
```

Run only the integration tests:

```bash
just int-test
```
```bash
uv run pytest tests/integration -v -W ignore::DeprecationWarning:discord.player
```

Generate coverage reports:

```bash
just coverage
```
```bash
uv run pytest -v --cov=main --cov-report=term-missing --cov-report=html -W ignore::DeprecationWarning:discord.player
```

## Linting

This project uses [ruff](https://docs.astral.sh/ruff/) on pull requests to ensure code is up to standard. You can run it locally with:

```bash
just lint
```
```bash
ruff check
```

It can also help resolve some issues through

```bash
just lint-fix
```
```bash
ruff check --fix
```

and
```bash
just format
```
```bash
ruff format
```

## Deployment

This project is currently deployed with Docker. On commit to the main branch GitHub actions builds and pushes new versions of the Docker image to the [photo-discord-bot packages](https://github.com/alpha-phi-omega-ez/photo-discord-bot/pkgs/container/photo-discord-bot)

## Authors

* [**Rafael Cenzano**](https://github.com/RafaelCenzano)

## License

This project is licensed under the AGPL License - see the [LICENSE](LICENSE) file for details

