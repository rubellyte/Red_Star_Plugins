from red_star.command_dispatcher import Command
from red_star.plugin_manager import BasePlugin
from red_star.rs_errors import CommandSyntaxError
from red_star.rs_utils import respond, JsonFileDict
from urllib.parse import urlparse
from urllib.request import urlopen
import mimetypes
from discord import File


class EmbedDict(dict):
    def to_dict(self):
        return self


def verify_embed(embed: dict):
    """
    A big ugly function to verify the embed dict as best as we can.
    Made even uglier by the verbosity I choose to include in the verification.
    :param embed:
    :return:
    """
    def option(res: dict, key: str, target: dict, scheme: (dict, int, None)):
        """
        Verification function.
        Scheme can be a length for text fields or None for url verification.
        Otherwise, it may be a dict of fields with either lengths or None for urls.
        :param res: "result" dict to put the values into
        :param key: Key to be verified
        :param target: The "embed" dict to pull values from
        :param scheme: Verification scheme
        :return: No returns, the result is added to res parameter. To prevent adding empty fields.
        """
        if key in target:
            if isinstance(scheme, dict):
                res[key] = {}
                for field in scheme:
                    if field not in target[key]:
                        continue
                    if scheme[field]:
                        if len(target[key][field]) > scheme[field]:
                            raise ValueError(f"{key}[{field}] too long. (limit {scheme[field]})")
                    else:
                        url = urlparse(target[key][field])
                        if not (url.scheme and url.netloc and url.path):
                            raise ValueError(f"{key}[{field}] invalid url.")
                    res[key][field] = target[key][field]
            else:
                if scheme:
                    if len(target[key]) > scheme:
                        raise ValueError(f"{key} too long. (limit {scheme})")
                else:
                    url = urlparse(target[key])
                    if not (url.scheme and url.netloc and url.path):
                        raise ValueError(f"{key} invalid url.")
                res[key] = target[key]

    result = {'type': 'rich'}
    option(result, 'title', embed, 256)
    option(result, 'description', embed, 2048)
    option(result, 'url', embed, None)
    option(result, 'image', embed, {"url": None})
    option(result, 'thumbnail', embed, {"url": None})
    option(result, 'footer', embed, {"text": 2048, "icon_url": None})
    option(result, 'author', embed, {"name": 256, "url": None, "icon_url": None})

    if 'color' in embed:
        try:
            result['color'] = int(embed['color'], 0)
        except TypeError:
            try:
                result['color'] = int(embed['color'])
            except ValueError:
                raise ValueError("Invalid color value.")

    if 'fields' in embed:
        result['fields'] = []
        for i, field in enumerate(embed['fields']):
            if not isinstance(field['inline'], bool):
                raise ValueError(f"Field {i+1}, \"inline\" must be true or false.")
            if len(str(field['name'])) > 256:
                raise ValueError(f"Field {i+1} \"name\" too long. (Limit 256)")
            if len(str(field['value'])) > 1024:
                raise ValueError(f"Field {i+1} \"value\" too long. (Limit 1024)")
            result['fields'].append({'name': str(field['name']), 'value': str(field['value']),
                                     'inline': field['inline']})

    return result


def verify_document(doc: list):
    """
    A helper function to verify entire documents.
    Verifies messages being of correct length, embeds being valid and file links being non-mangled.
    :param doc:
    :return:
    """
    result = []
    for i, msg in enumerate(doc):
        if isinstance(msg, str):
            if len(msg) > 2000:
                raise ValueError(f"Message {i+1} is too long. (Limit 2000)")
            result.append(msg)
        else:
            try:
                _msg = dict()
                if 'content' in msg:
                    if len(str(msg['content'])) > 2000:
                        raise ValueError(f"Message {i+1} is too long. (Limit 2000)")
                    _msg['content'] = str(msg['content'])
                if 'embed' in msg:
                    try:
                        _msg['embed'] = verify_embed(doc[i]['embed'])
                    except ValueError as e:
                        raise ValueError(f"Message {i+1} invalid embed: {e}")

                if 'file' in msg:
                    url = urlparse(msg['file'])
                    if not (url.scheme and url.netloc and url.path):
                        raise ValueError(f"Message {i+1} invalid attach url.")
                    _msg['file'] = msg['file']
                result.append(_msg)
            except TypeError:
                raise ValueError(f"Message {i+1} not an object or string.")
    return result


class ChannelPrint(BasePlugin):
    name = "channel_print"
    version = "1.0"
    author = "GTG3000"

    default_config = {
        "max_filesize": 1024 * 1024 * 8  # max 8 mb
    }

    walls: JsonFileDict

    async def activate(self):
        self.walls = self.config_manager.get_plugin_config_file("walls.json",
                                                                json_save_args={'indent': 2, 'ensure_ascii': False})

    @Command("Print",
             doc="Prints out the specified document from the storage, allowing to dump large amounts of information "
                 "into a channel, for example for purposes of a rules channel.",
             syntax="(document)",
             perms={"manage_messages"},
             delcall=True)
    async def _print(self, msg):
        gid = str(msg.guild.id)
        if gid not in self.walls:
            self.walls[gid] = dict()
            return

        try:
            wall = msg.clean_content.split(None, 1)[1]
        except IndexError:
            raise CommandSyntaxError

        if wall not in self.walls[gid]:
            raise CommandSyntaxError("No such document.")

        try:
            wall = verify_document(self.walls[gid][wall])
        except ValueError as e:
            await respond(msg, f"**WARNING: {e}**")
            return

        for post in wall:
            if isinstance(post, str):
                await respond(msg, post)
            else:
                _file = None
                if 'file' in post:
                    try:
                        _file = urlopen(post['file'])
                        if int(_file.info()['Content-Length']) > self.plugin_config['max_filesize']:
                            raise ValueError("File too big.")
                        _file = File(_file,
                                     filename="wallfile" + mimetypes.guess_extension(_file.info()['Content-Type']))
                    except Exception as e:
                        self.logger.info(f"Attachment file error in {msg.guild}:\n{e}")
                        await self.plugin_manager.hook_event("on_log_event", msg.guild,
                                                             f"**WARNING: Error occured during printout:**\n{e}",
                                                             log_type="print_event")
                        _file = None  # we just want to fail quietly, files are finnicky
                if _file or 'embed' in post or 'content' in post:
                    await respond(msg,
                                  post.get('content', None),
                                  embed=EmbedDict(post['embed']) if 'embed' in post else None,
                                  file=_file)

    @Command("PrintReload",
             doc="Reloads all documents from list. You probably shouldn't be using this too often.",
             bot_maintainers_only=True)
    async def _printreload(self, msg):
        self.walls.reload()
        await respond(msg, "**AFFIRMATIVE. Printout documents reloaded.**")

    # TODO: print pages uploading
    # TODO: print directly from attachment
    # TODO: documentation
