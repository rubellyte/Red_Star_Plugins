from red_star.plugin_manager import BasePlugin
from red_star.rs_utils import respond, verify_embed
from red_star.rs_errors import UserPermissionError
from red_star.command_dispatcher import Command


class ScreenShare(BasePlugin):
    name = "screenshare"
    description = "A plugin for providing a screenshare link."
    version = "1.0"
    author = "GTG3000"

    @Command("ScreenShare", "SShare",
             run_anywhere=True)
    async def _sshare(self, msg):
        try:
            voice_channel = msg.author.voice.channel
        except AttributeError:
            raise UserPermissionError("ANALYSIS: User is not connected to a voice channel.")

        embed = {
            "color": 0xFF0000,
            "description": f"**[Screen share link for {voice_channel}.](https://discordapp.com/channels/{msg.guild.id}/{voice_channel.id})**"
        }

        await respond(msg, embed=verify_embed(embed))
