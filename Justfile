# Common development commands for the photo-discord-bot project

set shell := ["/bin/sh", "-c"]

default:
    @just --list

# Install all dependencies (production + dev)
install:
    uv sync --extra dev

# Install only production dependencies
install-prod:
    uv sync

# Install/sync dev dependencies (includes production dependencies)
install-dev:
    uv sync --extra dev

# Run the Discord bot (requires configured environment)
run:
    uv run main.py

# Run the full test suite with pytest
test:
    uv run pytest -v -W ignore::DeprecationWarning:discord.player

# Run only the unit tests
unit-test:
    uv run pytest tests/unit -v -W ignore::DeprecationWarning:discord.player

# Run only the integration tests
int-test:
    uv run pytest tests/integration -v -W ignore::DeprecationWarning:discord.player

# Generate coverage reports (terminal + HTML)
coverage:
    uv run pytest -v --cov=main --cov-report=term-missing --cov-report=html -W ignore::DeprecationWarning:discord.player

# Remove coverage artifacts
coverage-clean:
    rm -f .coverage
    rm -rf htmlcov

# Lint the codebase with Ruff
lint:
    uv run ruff check

lint-fix:
    uv run ruff check --fix

format:
    uv run ruff format