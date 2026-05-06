##
## Filename: /home/utilisateur/Projects/SoraBot-V2/main.py
## Path: /home/utilisateur/Projects/SoraBot-V2
## Created Date: Monday, May 4th 2026, 11:07:48 am
## Description: Main entry point for SoraBot. Loads environment variables and starts the bot.
##

import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=".env")

from models.sorabot_class import sora_bot

sora_bot.run(os.getenv("TOKEN"))
