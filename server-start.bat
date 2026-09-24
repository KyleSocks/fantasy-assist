@echo off
cd /d "%~dp0"
title NHL Draft Assistant

uv run uvicorn src.api.main:app --port 8000

if errorlevel 1 (
    echo.
    echo Server exited with an error.
    pause
)