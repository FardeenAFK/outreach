@echo off
rem Starts the local Outreach server (the side panel talks to it). Safe to run twice.
cd /d "%~dp0"
start "" pythonw server.py
