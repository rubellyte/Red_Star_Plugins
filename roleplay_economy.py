from red_star.plugin_manager import BasePlugin
from red_star.command_dispatcher import Command
from red_star.rs_errors import CommandSyntaxError, UserPermissionError
from red_star.rs_utils import respond, RSArgumentParser, decode_json, split_output
from discord import Embed, File, Message, Reaction, Forbidden, NotFound
from io import BytesIO
from difflib import SequenceMatcher as SM
import shlex
import json
import re
from copy import deepcopy
from time import time


class RoleplayEconomy(BasePlugin):
    name = "roleplay_economy"
    version = "1.0"
    author = "GTG3000"

    default_config = {
        "shop_delay": 120
    }

    item_base = {
        "name": "Default Item",
        "description": "This item looks very generic",
        "category": None,
        "image": None,
        "inshop": False,
        "p_buy": 100,
        "p_sell": 100,
        "fields": {}
    }

    char_base = {
        "name": "Default Name",
        "image": "",
        "owner": 0,
        "money": 0,
        "inv": [],
        "inv_key": [],
        "fields": {}
    }

    item_template = {
        "name": "default_item",
        "override": {},
        "count": 1
    }

    shop_template = {
        "user": 0,          # user ID
        "page": 0,          # the current page
        "mpages": 0,        # the max page (pre-calculated because fuck that)
        "category": "",     # the category of items to display
        "time": 0,          # time.time() to auto-purge the message (the message ID is the key)
        "message": None     # the message object, for deletions.
    }

    shops = {}  # self.shops[gid][message_id]: self.Shop

    # to lower performance impact of disk IO, move the saving of modified character files to the tick event.
    # technically, if the bot is shut down properly, we don't need to save them manually at all - but you never know.
    # not doing the same for items because frankly I don't see any reason for items to be modified so often.
    _save_chars = False

    class Character:
        _parent = None
        _chars = None
        shop = None
        gid = None
        id = None

        _fields = ['owner', 'name', 'image', 'money', 'inv', 'inv_key', 'fields']

        def __init__(self, parent, guild: str, character: str):
            self._parent = parent
            # init the guild, to save up on _initialize calls
            self._parent._initchar(guild)
            self._parent._inititem(guild)
            self.gid = guild
            self._chars = self._parent.chars[guild]
            self.shop = self._parent.shop_items[guild]
            # init the character, for same reasons
            _char = character.lower()
            self._parent._initbio(self.gid, _char)
            self.id = _char

        def __getattr__(self, item):
            if item in self._fields:
                return self._chars[self.id][item]
            else:
                raise AttributeError

        def __setattr__(self, key, value):
            if key in self._fields:
                self._chars[self.id][key] = value
            else:
                object.__setattr__(self, key, value)

        def get_item(self, query: [int, str], key: bool = False):
            """
            Function to find an item in (key) inventory of the character, by index, name or id
            :param query: index, name or id of the item
            :param key: whether or not to search in key inventory
            :return:
            """
            try:
                item = (self.inv_key if key else self.inv)[int(query) - 1]
            except ValueError:
                try:
                    item = [i for i in (self.inv_key if key else self.inv) if i['name'] == query or
                            i['override'].get('name', self.shop[i['name']]['name']).lower() == query].pop()
                except IndexError:
                    raise CommandSyntaxError(f"No such item: {query}.")
            return item

        def stack_item(self, item: dict, key: bool = False):
            """
            Function to add a given item to the (key) inventory of the character.
            Ensures same items are stacked, allows taking items through negative counts, adds new items and purges
            empty stacks.
            :param item: {'name':'', 'override':{}, 'count': 0}
            :param key: whether or not to search in key inventory
            :return:
            """
            inv = self.inv_key if key else self.inv
            for _pos, _item in enumerate(inv):
                if item['name'] == _item['name'] and item['override'] == _item['override']:
                    inv[_pos]['count'] += item['count']
                    if inv[_pos]['count'] <= 0:
                        del inv[_pos]
                    return
            else:
                if item['count'] > 0:
                    inv.append(item)

        def override_item(self, item: dict):
            return {**self.shop[item['name']], **item['override'],
                    'fields': [(f, v) for f, v in
                               list({**dict(self.shop[item['name']]['fields']),
                                     **dict(item['override'].get('fields', []))}.items())
                               if v]}

        def embed(self):
            t_embed = Embed(type="rich", colour=16711680)
            t_embed.title = self.name
            if self.image:
                t_embed.set_thumbnail(url=self.image)
            for (f_name, f_data) in self.fields:
                t_embed.add_field(name=f_name, value=f_data, inline=False)

            t_str = ""

            for item in self.inv_key[:32]:
                if item['name'] in self.shop:
                    _n = item['override'].get('name')
                    t_str += _n[:23] + '*' if _n else self.shop[item['name']]['name'][:24]
                else:
                    t_str += f"{item['name'][:24]:-^24}"
                t_str = f"{t_str:<24}×{item['count']:5d}\n" if item['count'] > 1 else f"{t_str}\n"
            if len(self.inv_key) > 32:
                t_str += f"And {len(self.inv_key)-32} more..."

            if t_str:
                t_embed.add_field(name="Key Items", value=f"```\n{t_str}```")

            t_str = ""

            for item in self.inv[:32]:
                if item['name'] in self.shop:
                    _n = item['override'].get('name')
                    t_str += _n[:23] + '*' if _n else self.shop[item['name']]['name'][:24]
                else:
                    t_str += f"{item['name'][:24]:-^24}"
                t_str = f"{t_str:<24}×{item['count']:5d}\n" if item['count'] > 1 else f"{t_str}\n"
            if len(self.inv) > 32:
                t_str += f"And {len(self.inv)-32} more..."

            if t_str:
                t_embed.add_field(name="Items", value=f"```\n{t_str}```")

            t_embed.set_footer(text=f"Money: {self.money}")

            return t_embed

    class Shop:
        user: int
        page: int
        category: str
        time: float = 0

        _mpage: int
        _gid: str
        _parent = None
        _message: Message

        _items: list

        _emoji = "◀🇽▶"
        _len = 10

        def __init__(self, parent, user: int, gid: str, category: str=""):
            self._parent = parent
            self.user = user
            self.category = category

            self.time = time()
            self._gid = gid
            self.page = 0

            # pregenerate the item text lines. This is an expensive-ish operation and it's better to cache it.

            items = [x for x in parent.shop_items[self._gid].values()
                     if x['inshop'] and (x['category'] == category or not category)]

            if len(items) % 2 == 1:
                items.append({'name': "", 'p_buy': 0, 'p_sell': 0})

            self._items = [
                f"{items[i]['name']:^24}{items[i+1]['name']:^24}\n"
                f"[Buy: {items[i]['p_buy']:5d} Sell: {items[i]['p_sell']:5d}]"
                f"[Buy: {items[i+1]['p_buy']:5d} Sell: {items[i+1]['p_sell']:5d}]\n"
                for i in range(0, len(items), 2)]


            self._mpage = len([x for x in parent.shop_items[self._gid].values()
                               if x['inshop'] and (x['category'] == category or not category)]) // self._len

        async def post(self, msg):
            self._message = await respond(msg, self.text())
            await self._message.add_reaction('◀')
            await self._message.add_reaction('🇽')
            await self._message.add_reaction('▶')
            self._parent.shops[self._gid][self._message.id] = self

        def text(self):
            text = "```asciidoc\n" \
                   f"{'.🙡Item Shop🙣.':^48}\n{'='*48}\n"
            text += (('// ' + self.category + '\n\n') if self.category else '\n')
            text += "\n".join(self._items[self.page*self._len:self.page*self._len+self._len])
            p = f"[Page {self.page+1} of {self._mpage}]"
            text += f"\n\n{p:^48}```"
            return text

        async def react(self, reaction: Reaction):
            if reaction.emoji == '🇽':
                del self._parent.shops[self._gid][self._message.id]
                await self._message.delete()
            elif reaction.emoji == '◀' and self.page > 0:
                self.page -= 1
                self.time = time()
                await self._message.edit(content=self.text())
            elif reaction.emoji == '▶' and self.page < self._mpage-1:
                self.page += 1
                self.time = time()
                await self._message.edit(content=self.text())

    async def activate(self):
        self.bios = self.plugins['roleplay'].bios if 'roleplay' in self.plugins else None
        self.chars = self.config_manager.get_plugin_config_file("econ_chars.json",
                                                                json_save_args={"indent": 2, 'ensure_ascii': False})
        self._initchar('default')
        self.shop_items = self.config_manager.get_plugin_config_file("econ_items.json",
                                                                     json_save_args={"indent": 2,
                                                                                     'ensure_ascii': False})
        self._inititem('default')

# Util Functions

    def _initchar(self, gid):
        if gid not in self.chars:
            self.chars[gid] = self.chars.get('default', {'default_char': self.char_base})

    def _inititem(self, gid):
        if gid not in self.shop_items:
            self.shop_items[gid] = self.shop_items.get('default', {'default_item': self.item_base})

    def _initbio(self, gid, name):
        if name not in self.chars[gid]:
            if self.bios and name in self.bios.get(gid, {}):
                self.chars[gid][name] = self.chars[gid].get('default_char', self.char_base)
                self.chars[gid][name]['name'] = self.bios[gid][name]['name']
                self.chars[gid][name]['image'] = self.bios[gid][name]['image']
                self.chars[gid][name]['owner'] = self.bios[gid][name]['author']
            else:
                raise CommandSyntaxError(f"No such character: {name}")

    @staticmethod
    def _generate_item_embed(item: dict, custom=False):
        """
        Helper function to generate item embeds, given (overriden) item data.
        The function does not do it's own overriding on account of that requiring it be tied to Character class.
        :param item:
        :param custom:
        :return:
        """

        item_embed = Embed(type="rich", colour=16711680)
        item_embed.title = item['name'] + (" ★" if custom else "")
        item_embed.description = f"{item['description']}\n\nPrice: {item['p_buy']}/{item['p_sell']}"
        if item['image']:
            item_embed.set_thumbnail(url=item['image'])
        for (f_name, f_data) in item["fields"]:
            item_embed.add_field(name=f_name, value=f_data, inline=False)

        if item['category']:
            item_embed.set_footer(text=item['category'])

        return item_embed

    @staticmethod
    def _verify_item(item):

        # fields : name, category, description, image, inshop, p_buy, p_sell, fields {}
        # just make sure they're at least correct length/type in general.
        try:
            output = {}
            if 'name' in item:
                output['name'] = str(item['name'])[:24]
            if 'category' in item:
                output['category'] = str(item['category'])[:24]
            if "description" in item:
                output["description"] = str(item["description"])[:1000]
            if "image" in item:
                output["image"] = item["image"]
            if "inshop" in item:
                output["inshop"] = bool(item["inshop"])
            if "p_buy" in item:
                try:
                    output["p_buy"] = max(0, int(item["p_buy"]))
                except ValueError:
                    raise CommandSyntaxError(f"Buying price not a valid integer: {item['p_buy']}.")
            if "p_sell" in item:
                try:
                    output["p_sell"] = max(0, int(item["p_sell"]))
                except ValueError:
                    raise CommandSyntaxError(f"Selling price not a valid integer: {item['p_sell']}.")
            if "fields" in item:
                try:
                    output['fields'] = [[str(name)[:32], str(value)[:1024]] for name, value in item['fields']]
                except (ValueError, TypeError):
                    raise CommandSyntaxError("Fields must be lists of two values each.")
            return output
        except (KeyError, TypeError):
            raise CommandSyntaxError('Item or Override must be a dict.')

    def _find_item(self, gid: str, query: str):
        """
        Function to finda possible item using sequence matcher.
        Collects possible candidates and returns them too, but don't count on that if it does find a match.
        :param gid:
        :param query:
        :return:
        """
        # no fancy data class this time
        _s = self.shop_items[gid]

        item = None
        possible = []
        if query not in _s:
            for key, content in _s.items():
                match = SM(None, query, content['name'].lower()).ratio()
                if match > 0.9:
                    item = key
                    break
                elif match > 0.5:
                    possible.append(content['name'])
        else:
            item = query

        return item, possible

    def _verify_char(self, char: dict, default: dict):
        try:
            # char_base = {
            #     "name": "Default Name",
            #     "image": "",
            #     "owner": 0,
            #     "money": 0,
            #     "inv": [],
            #     "inv_key": [],
            #     "fields": []
            # }

            output = dict()
            output['name'] = char.get('name', default['name'])[:32]
            output['image'] = char.get('image', default['image'])
            output['owner'] = int(char.get('owner', default['owner']))
            output['money'] = max(0, int(char.get('money', default['money'])))

            # All items have to be at *least* valid in their format.
            # We don't strip items that have invalid base item ids, but we do strip ones that have negative counts.
            if 'inv' in char:
                output['inv'] = [
                    {'name': str(i['name']), 'override': self._verify_item(i['override']), 'count': int(i['count'])}
                    for i in char['inv'] if int(i['count']) > 0
                ]
            else:
                output['inv'] = deepcopy(default['inv'])
            if 'inv_key' in char:
                output['inv_key'] = [
                    {'name': str(i['name']), 'override': self._verify_item(i['override']), 'count': int(i['count'])}
                    for i in char['inv_key'] if int(i['count']) > 0
                ]
            else:
                output['inv_key'] = deepcopy(default['inv_key'])
            if 'fields' in char:
                try:
                    output['fields'] = [[str(name)[:32], str(value)[:1024]] for name, value in char['fields']]
                except (ValueError, TypeError):
                    raise CommandSyntaxError("Fields must be lists of two string values each.")
            else:
                output['fields'] = deepcopy(default['fields'])

            return output

        except (KeyError, TypeError):
            raise CommandSyntaxError('Character must be a dict.')

# Commands

    @Command('Char',
             doc="Prints information about characters and their inventories,"
                 " down to specific items, and allows item transfer.\n"
                 "When called with no flags, prings a character sheet embed.\n\n"
                 "-[-i]tem/-[-k]eyitem: prints information about an item from either inventory, useful to see "
                 "information about custom items.\n"
                 "  -[-d]ump: dumps the selected item information, for backup purposes.\n"
                 "  -[-g]ive: transfers the item to another character, with optional amount.\n"
                 "  -[-s]ell: sells the selected item, with optional amount.\n"
                 "-[-d]ump: when used without -i flag, dumps the character information, for backup purposes.\n"
                 "-[-p]ay : transfers money to a specified character, with a specified amount.\n"
                 "-[-b]uy : purchases specified item from the shop, with optional amount.",
             syntax="(name) [-[-i]tem/-[-k]eyitem (pos/item name) [-[-d]ump] [-[-g]ive recipient [amount]] "
                    "[-[-s]ell [amount]]] [-[-d]ump] [-[-p]ay (recipient) (amount)] [-[-b]uy (item) [amount]]",
             category="role_play_economy")
    async def _char(self, msg):
        gid = str(msg.guild.id)

        parser = RSArgumentParser()
        parser.add_argument("command")
        parser.add_argument("char", nargs='+', default=[])
        item_args = parser.add_mutually_exclusive_group()
        item_args.add_argument('-i', '--item', nargs='+', default=[])
        item_args.add_argument('-k', '--keyitem', nargs='+', default=[])
        item_manip = parser.add_mutually_exclusive_group()
        item_manip.add_argument('-g', '--give', nargs='+')
        item_manip.add_argument('-s', '--sell', nargs='?', const='1')
        parser.add_argument('-d', '--dump', action='store_true')
        parser.add_argument('-p', '--pay', nargs=2)
        parser.add_argument('-b', '--buy', nargs='+')

        try:
            args = parser.parse_args(shlex.split(msg.clean_content))
        except ValueError as e:
            self.logger.warning(f"Unable to split {msg.clean_content}. {e}")
            raise CommandSyntaxError(e)

        args['char'] = ' '.join(args['char']).lower()
        args['item'] = ' '.join(args['item']).lower()
        args['keyitem'] = ' '.join(args['keyitem']).lower()

        # creating a Character instance automatically inits all relevant data.
        # Raises KeyError if character doesn't exist and can't be created automatically from bios
        char = self.Character(self, gid, args['char'])

        if args['item'] or args['keyitem']:
            # enter the depths of item manipulation.
            # key inventory takes preference, but otherwise the items are treated about same.
            item = char.get_item(args['keyitem'] or args['item'], bool(args['keyitem']))

            if args['dump']:
                # char (name) -i/k (item) -d
                # -d is a multi-purpose flag, and with -i/k it's used to create backups of custom items.
                # or you know, grabbing them to edit.
                async with msg.channel.typing():
                    await respond(msg, "**AFFIRMATIVE. File upload completed.**",
                                  file=File(BytesIO(bytes(json.dumps(item, indent=2, ensure_ascii=False),
                                                          encoding="utf8")),
                                            filename=item['name']+'.json'))
            elif args['give']:
                # char (name) -i/k (item) -g (name) [amount]
                # attempts to give an item to another character. We assume that char ids are one word long.
                # or they can be wrapped in quotes with shlex.
                if args['keyitem']:
                    raise UserPermissionError('Can not give away key items.')

                recipient = self.Character(self, gid, args['give'][0].lower())
                try:
                    amount = int(args['give'][1]) if len(args['give']) > 1 else 1
                except ValueError:
                    raise CommandSyntaxError(f"{args['give'][1]} is not a valid amount.")

                if amount > item['count']:
                    raise UserPermissionError("Can not give more items than you possess.")
                elif amount < 1:
                    raise UserPermissionError("Can not take items this way.")

                recipient.stack_item({**item, "count": amount})
                char.stack_item({**item, "count": -amount})
                self._save_chars = True

                item_string = f"{amount} {char.override_item(item)['name']}s" if amount > 1 else \
                    char.override_item(item)['name']

                await respond(msg, f"**AFFIRMATIVE. {item_string} given to {recipient.name}.**")
            elif args['sell']:
                # char (name) -i/k (item) -s [amount]
                # exactly what it says on the tin - determine prices, determine amount, prevent selling items with
                # no price or that you don't have.
                _item = char.override_item(item)
                price = _item['p_sell']
                name = _item['name']
                if args['keyitem']:
                    raise UserPermissionError('Can not sell key items.')

                if price <= 0:
                    await respond(msg, "**WARNING: Failed to find buyer for your item.**")
                    return

                try:
                    amount = int(args['sell'])
                except ValueError:
                    raise CommandSyntaxError(f"{args['sell']} is not a valid amount.")

                if amount > item['count']:
                    raise UserPermissionError("Can not sell more items than you possess.")
                elif amount < 1:
                    raise UserPermissionError("Can not buy items this way.")

                if item['override']:
                    self.logger.info(f"Overriden item sold:\n\n{json.dumps(item)}")
                char.stack_item({**item, "count": -amount})
                char.money += price * amount
                self._save_chars = True

                await respond(msg, f"**AFFIRMATIVE. {amount} {name}{'s' if amount>1 else ''} "
                                   f"sold for {price*amount} currency.**")
            else:
                await respond(msg, embed=self._generate_item_embed(char.override_item(item), bool(item['override'])))
        elif args['dump']:
            # the second part of the multi-purpose -d flag. Dumps character for backup/editing.
            async with msg.channel.typing():
                await respond(msg, "**AFFIRMATIVE. File upload completed.**",
                              file=File(BytesIO(bytes(json.dumps(char._chars[char.id], indent=2, ensure_ascii=False),
                                                      encoding="utf8")),
                                        filename=char.id+'.json'))
        elif args['pay']:
            # char (name) -p (name) (amount)
            # transfers money between characters, pretty straightforward.
            try:
                amount = int(args['pay'][1])
                if not 0 < amount < char.money:
                    raise ValueError
            except ValueError:
                raise CommandSyntaxError(f"{args['pay'][1]} is not a valid amount.")

            recipient = self.Character(self, gid, args['pay'][0].lower())
            recipient.money += amount
            char.money -= amount
            self._save_chars = True
            await respond(msg, f"**AFFIRMATIVE. {amount} currency transferred to {recipient.name}.**")
        elif args['buy']:
            # char (name) -b (item) [amount]
            # attempts to buy a number of items from the shop

            try:
                amount = max(1, int(args['buy'][1]))
            except IndexError:
                amount = 1
            except ValueError:
                raise CommandSyntaxError(f"{args['buy'][1]} is not a valid amount.")

            item, _ = self._find_item(gid, args['buy'][0])

            if item and char.shop[item]['inshop'] and char.shop[item]['p_buy'] > 0:
                if char.money > amount * char.shop[item]['p_buy']:
                    char.stack_item({'name': item, 'override': {}, 'count': amount})
                    char.money -= amount * char.shop[item]['p_buy']
                    self._save_chars = True
                    await respond(msg, f"**AFFIRMATIVE. {amount} {char.shop[item]['name']} purchased.**")
                else:
                    await respond(msg, "**WARNING: Insufficient funds.**")
            else:
                await respond(msg, "**NEGATIVE. No such item.**")
        elif args['sell']:
            # char (name) -s (item/pos) [amount]
            # in case you forget that -i (item) -s [amount] is the syntax.

            item = char.get_item(args['sell'][0])
            _item = char.override_item(item)
            price = _item['p_sell']
            name = _item['name']

            if price <= 0:
                await respond(msg, "**WARNING: Failed to find buyer for your item.**")
                return

            try:
                amount = max(1, int(args['sell'][1]))
            except IndexError:
                amount = 1
            except ValueError:
                raise CommandSyntaxError(f"{args['buy'][1]} is not a valid amount.")

            if amount > item['count']:
                raise UserPermissionError("Can not sell more items than you possess.")
            elif amount < 1:
                raise UserPermissionError("Can not buy items this way.")

            if item['override']:
                self.logger.info(f"Overriden item sold:\n\n{json.dumps(item)}")

            char.stack_item({**item, "count": -amount})
            char.money += price * amount
            self._save_chars = True

            await respond(msg, f"**AFFIRMATIVE. {amount} {name}{'s' if amount>1 else ''} "
                               f"sold for {price*amount} currency.**")
        else:
            await respond(msg, embed=char.embed())

    @Command('Give', 'GiveKey',
             doc="Gives the specified character a specified item. You can specify item by it's ID or name.\n"
                 "Use \"GiveKey\" alias to add items to the characters key inventory.\n"
                 "Use \"money\" item to give characters currency.",
             syntax="(name) (item) [amount]",
             perms={"manage_messages"},
             category="role_play_economy")
    async def _give(self, msg):
        gid = str(msg.guild.id)
        parser = RSArgumentParser()
        parser.add_argument('command')
        parser.add_argument('name')
        parser.add_argument('item')
        parser.add_argument('amount', nargs='?', default=1, type=int)

        try:
            args = parser.parse_args(shlex.split(msg.clean_content))
        except ValueError as e:
            self.logger.warning(f"Unable to split {msg.clean_content}. {e}")
            raise CommandSyntaxError(e)

        if args['amount'] < 1:
            raise CommandSyntaxError("Please use the Take command to remove items.")

        char = self.Character(self, gid, args['name'])

        query = args['item'].lower()

        if query == 'money':
            char.money += args['amount']
            self._save_chars = True
            await respond(msg, f"**AFFIRMATIVE. {args['amount']} currency given to {char.name}.**")
        else:
            item, possible = self._find_item(gid, query)
            if item:
                char.stack_item({"name": query, "override": {}, "count": args['amount']},
                                args['command'].endswith('key'))
                self._save_chars = True
                await respond(msg, f"**AFFIRMATIVE. {args['amount']} "
                                   f"{char._items[query]['name']} given to {char.name}.**")
            elif possible:
                await split_output(msg,  "**ANALYSIS: Perhaps you meant one of these items?**", possible)
            else:
                await respond(msg, f"**WARNING: Could not find item: {query}.**")

    @Command('Take', 'TakeKey',
             doc="Takes specified items from a specified character. Items can be specified by ID, name or position "
                 "in the inventory.\n"
                 "Use \"TakeKey\" alias to take items from the characters key inventory."
                 "Use \"money\" item to take characters currency.",
             syntax="(name) (item) [amount]",
             perms={"manage_messages"},
             category="role_play_economy")
    async def _take(self, msg):
        gid = str(msg.guild.id)

        parser = RSArgumentParser()
        parser.add_argument('command')
        parser.add_argument('name')
        parser.add_argument('item')
        parser.add_argument('amount', nargs='?', default=1, type=int)

        try:
            args = parser.parse_args(shlex.split(msg.clean_content))
        except ValueError as e:
            self.logger.warning(f"Unable to split {msg.clean_content}. {e}")
            raise CommandSyntaxError(e)

        if args['amount'] < 1:
            raise CommandSyntaxError("Please use the Give command to add items.")

        char = self.Character(self, gid, args['name'])
        query = args['item'].lower()
        key = args['command'].endswith('key')

        if query == 'money':
            char.money -= args['amount']
            await respond(msg, f"**AFFIRMATIVE. {args['amount']} currency taken from {char.name}.**")
        else:
            try:
                # presumably, item refers to one of the items in the characters inventory.
                # by id or name, possibly overridden.
                item = [i for i in (char.inv_key if key else char.inv) if i['name'] == query or
                        i['override'].get('name', char.shop[i['name']]['name']).lower() == query].pop()
            except IndexError:
                try:
                    # okay, maybe it'll just be a position? Maybe?
                    item = (char.inv_key if key else char.inv)[int(query)-1]
                except ValueError:
                    raise CommandSyntaxError(f"No item with id/name/position {query}.")
            char.stack_item({**item, "count": -args['amount']}, key)
            self._save_chars = True

            await respond(msg, f"**AFFIRMATIVE. {args['amount']} {char.override_item(item)['name']} "
                               f"taken from {char.name}**")

    @Command("GiveCustom", "GiveKeyCustom",
             doc="Gives a specified character a customised item.\n"
                 "Item can be given as a JSON file or a JSON code block.\n"
                 "See output of char item dump for inventory item format.\n"
                 "Override format is exactly same as shop item format.",
             syntax="(name) [json code block/file]",
             perms={"manage_messages"},
             category="role_play_economy")
    async def _givecustom(self, msg):
        gid = str(msg.guild.id)

        # to allow both names with spaces and code blocks, we split the message once.
        # if there's a code block, it'll be detected in the latter part of the code with a regex.
        # if there's both an attachment and a code block, too bad.
        try:
            args = msg.content.split(None, 1)
            key = args[0].lower().endswith("keycustom")
            data = args[1]
        except IndexError:
            raise CommandSyntaxError

        if msg.attachments:
            # If there's an attachment, assume that the entire arg string is a character name and try to parse the
            # attached JSON
            _file = BytesIO()

            char = data
            await msg.attachments[0].save(_file)
            try:
                data = decode_json(_file.getvalue())
            except ValueError as e:
                self.logger.exception("Could not decode uploaded bio file!", exc_info=True)
                raise CommandSyntaxError(e)
            except Exception as e:
                raise CommandSyntaxError(f"Not a valid JSON file: {e}.\n")
        else:
            # Otherwise, there's a code block attached. If there isn't, welp.
            data = re.match("(?P<char>.+?)\s+```.*?(?P<json>{.+}).*```", data, re.DOTALL)
            char = data['char']
            try:
                data = json.loads(data['json'])
            except Exception as e:
                raise CommandSyntaxError(f"Not a valid JSON block: {e}.\n")

        char = self.Character(self, gid, char.lower())
        try:
            # all items need SOME sort of a base item that they can override from.
            if data['name'] not in char.shop:
                raise CommandSyntaxError("Base item not found.")
            # and we don't want any other garbage users may upload.
            item = {
                'name': data['name'],
                'override': self._verify_item(data['override']),
                'count': int(data['count'])
            }
            char.stack_item(item, key)
            self._save_chars = True
            await respond(msg, f"**AFFIRMATIVE. {item['count']} {char.override_item(item)['name']} given to "
                               f"{char.name}.**")
        except (KeyError, ValueError):
            raise CommandSyntaxError("Incorrectly formatted item.")

    @Command("DumpItem",
             doc="Dumps item information as a JSON file.\n"
                 "Item can be specified by ID or name. This command will use fuzzy search in case you don't write "
                 "the name precisely right.",
             syntax="(item)",
             category="role_play_economy")
    async def _dumpitem(self, msg):
        gid = str(msg.guild.id)
        self._inititem(gid)

        try:
            item = msg.clean_content.split(None, 1)[1].lower()
        except IndexError:
            raise CommandSyntaxError("Item name required.")

        item, possible = self._find_item(gid, item)

        if item:
            await respond(msg, "**AFFIRMATIVE. Dumping following item:**",
                          embed=self._generate_item_embed(self.shop_items[gid][item]),
                          file=File(BytesIO(bytes(json.dumps(self.shop_items[gid][item], ensure_ascii=False, indent=2),
                                                  encoding="utf8")),
                                    filename=item + '.json'))
        elif possible:
            await split_output(msg,  "**ANALYSIS: Perhaps you meant one of these items?**", possible)
        else:
            await respond(msg, f"**WARNING: Could not find item: {item}.**")

    @Command("ItemInfo",
             doc="Prints out shop item information embed.\n"
                 "Item can be specified by ID or name. This command will use fuzzy search in case you don't write "
                 "the name precisely right.",
             syntax="(item)",
             category="role_play_economy")
    async def _iteminfo(self, msg):
        gid = str(msg.guild.id)
        self._inititem(gid)

        try:
            item = msg.clean_content.split(None, 1)[1].lower()
        except IndexError:
            raise CommandSyntaxError("Item name required.")

        item, possible = self._find_item(gid, item)

        if item:
            await respond(msg, embed=self._generate_item_embed(self.shop_items[gid][item]))
        elif possible:
            await split_output(msg,  "**ANALYSIS: Perhaps you meant one of these items?**", possible)
        else:
            await respond(msg, f"**WARNING: Could not find item: {item}.**")

    @Command("UploadItem",
             doc="Creates or updates item specified with an id. ID must be without whitespace.\n"
                 "Item can be given as a JSON file or a JSON code block.\n"
                 "See output of dumpitem command for format.",
             syntax="(id) [json code block/file]",
             perms={"manage_messages"},
             category="role_play_economy",
             run_anywhere=True)
    async def _uploaditem(self, msg):
        gid = str(msg.guild.id)
        self._inititem(gid)

        try:
            data = msg.content.split(None, 1)[1]
        except IndexError:
            raise CommandSyntaxError

        if msg.attachments:
            # If there's an attachment, assume that the entire arg string is an item id and try to parse the
            # attached JSON
            _file = BytesIO()

            # no spaces allowed in IDs. >:T
            item = data.split()[0]
            await msg.attachments[0].save(_file)
            try:
                data = decode_json(_file.getvalue())
            except ValueError as e:
                self.logger.exception("Could not decode uploaded item file!", exc_info=True)
                raise CommandSyntaxError(e)
            except Exception as e:
                raise CommandSyntaxError(f"Not a valid JSON file: {e}.\n")
        else:
            # Otherwise, there's a code block attached. If there isn't, welp.
            # matches first word after command and then the object from inside the codeblock.
            data = re.match("(?P<item>[^\s]+).+```.*?(?P<json>{.+}).*```", data, re.DOTALL)
            item = data['item']
            try:
                data = json.loads(data['json'])
            except Exception as e:
                raise CommandSyntaxError(f"Not a valid JSON block: {e}.\n")

        data = {**self.shop_items[gid]['default_item'], **self._verify_item(data)}
        u_str = "updated" if item in self.shop_items[gid] else "created"

        self.shop_items[gid][item] = data
        self.shop_items.save()

        await respond(msg, f"**AFFIRMATIVE. {data['name']} with id {item} {u_str}.**",
                      embed=self._generate_item_embed(data))

    @Command("DeleteItem",
             doc="Removes the specified item, specified by ID or name. This command will use fuzzy search in case "
                 "you don't write the name precisely right.",
             syntax="(item)",
             perms={"manage_messages"},
             category="role_play_economy",
             run_anywhere=True)
    async def _deleteitem(self, msg):
        gid = str(msg.guild.id)
        self._inititem(gid)

        try:
            data = msg.content.split(None, 1)[1]
        except IndexError:
            raise CommandSyntaxError

        item, possible = self._find_item(gid, data)

        if item:
            i = self.shop_items[gid].pop(item)
            self.shop_items.save()
            await respond(msg, f"**AFFIRMATIVE. Item {item} deleted.**", embed=self._generate_item_embed(i))
        elif possible:
            await split_output(msg,  "**ANALYSIS: Perhaps you meant one of these items?**", possible)
        else:
            await respond(msg, f"**WARNING: Could not find item: {item}.**")

    @Command("ListItems",
             doc="Prints a list of all items.",
             perms={"manage_messages"},
             category="role_play_economy",
             run_anywhere=True)
    async def _listitems(self, msg):
        gid = str(msg.guild.id)

        items = (f"{i['name']:^24}: {i_id}" for i_id, i in self.shop_items[gid].items())

        await split_output(msg, "**AFFIRMATIVE. Listing off all items:**", items)

    @Command("UploadChar",
             doc="Creates or updates character specified with an id.\n"
                 "Character can be given as a JSON file or a JSON code block.\n"
                 "See output of char -d command for format.",
             perms={"manage_messages"},
             category="role_play_economy",
             run_anywhere=True)
    async def _uploadchar(self, msg):
        gid = str(msg.guild.id)
        self._initchar(gid)

        try:
            data = msg.content.split(None, 1)[1]
        except IndexError:
            raise CommandSyntaxError

        if msg.attachments:
            # If there's an attachment, assume that the entire arg string is an item id and try to parse the
            # attached JSON
            _file = BytesIO()

            char = data
            await msg.attachments[0].save(_file)
            try:
                data = decode_json(_file.getvalue())
            except ValueError as e:
                self.logger.exception("Could not decode uploaded character file!", exc_info=True)
                raise CommandSyntaxError(e)
            except Exception as e:
                raise CommandSyntaxError(f"Not a valid JSON file: {e}.\n")
        else:
            # Otherwise, there's a code block attached. If there isn't, welp.
            # matches first word after command and then the object from inside the codeblock.
            data = re.match("(?P<char>.+?)\s+```.*?(?P<json>{.+}).*```", data, re.DOTALL)
            char = data['char']
            try:
                data = json.loads(data['json'])
            except Exception as e:
                raise CommandSyntaxError(f"Not a valid JSON block: {e}.\n")

        data = self._verify_char(data, self.chars[gid]['default_char'])
        u_str = "updated" if char in self.chars[gid] else "created"

        self.chars[gid][char] = data
        self._save_chars = True

        data = self.Character(self, gid, char)

        await respond(msg, f"**AFFIRMATIVE. Character {data.name} with id {char} {u_str}.**",
                      embed=data.embed())

    @Command("DeleteChar",
             doc="Deletes a character specified with an id.",
             perms={"manage_messages"},
             category="role_play_economy",
             run_anywhere=True)
    async def _deletechar(self, msg):
        gid = str(msg.guild.id)
        self._initchar(gid)

        try:
            char = msg.content.split(None, 1)[1]
        except IndexError:
            raise CommandSyntaxError

        if char in self.chars:
            char = self.chars.pop(char)
            self._save_chars = True
            await respond(msg, f"**AFFIRMATIVE. Character {char['name']} deleted.**")
        else:
            possible = [ID for ID in self.chars if SM(None, ID, char).ratio() > 0.5]
            if possible:
                await split_output(msg, f"**ANALYSIS: No character {char} found. Perhaps you meant one of the "
                                        f"following:**", possible)
            else:
                await respond(msg, f"**WARNING: No character {char} found.**")

    @Command("ListChars",
             doc="Prints a list of all characters.",
             perms={"manage_messages"},
             category="role_play_economy",
             run_anywhere=True)
    async def _listchars(self, msg):
        gid = str(msg.guild.id)

        chars = (f"{i['name']:^32}: {i_id}" for i_id, i in self.chars[gid].items())

        await split_output(msg, "**AFFIRMATIVE. Listing off all chars:**", chars)

    @Command("Shop",
             doc="Generates a shop interface for browsing the available items.\n"
                 "Accepts an optional category argument.",
             syntax="[category]",
             category="role_play_economy")
    async def _shop(self, msg):
        gid = str(msg.guild.id)

        try:
            category = msg.clean_content.split(None, 1)[1].lower()
        except IndexError:
            category = ""

        if gid not in self.shops:
            self.shops[gid] = {}
        t_shop = self.Shop(self, msg.author.id, gid, category)
        await t_shop.post(msg)

    @Command("ShopCategories",
             doc="Lists all available shop categories.",
             category="role_play_economy")
    async def _shopcategories(self, msg):
        gid = str(msg.guild.id)
        await split_output(msg,
                           "**AFFIRMATIVE. Listing categories:**",
                           {*[x['category'] for x in self.shop_items[gid].values() if x['category'] and x['inshop']]})

    @Command("ShopItems",
             doc="Lists all available shop items.",
             syntax="[category]",
             category="role_play_economy")
    async def _shopitems(self, msg):
        gid = str(msg.guild.id)
        try:
            selector = msg.clean_content.split(None, 1)[1].lower()
        except IndexError:
            selector = False

        output = sorted([f"{x['name']:^24}: {x['p_buy']}" for x in self.shop_items[gid].values()
                         if x['inshop'] and (x['category'] == selector or not selector)])
        await split_output(msg, "**ANALYSIS: items in requested category:**" if selector else
                                "**AFFIRMATIVE. Listing shop items:**",
                           output)

    @Command("ReloadEconChars",
             doc="Reloads characters from disk.",
             category="role_play_economy",
             bot_maintainers_only=True)
    async def _reloadchars(self, msg):
        self.chars.reload()
        await respond(msg, "**AFFIRMATIVE. Characters reloaded.**")

    @Command("ReloadEconItems",
             doc="Reloads items from disk.",
             category="role_play_economy",
             bot_maintainers_only=True)
    async def _reloaditems(self, msg):
        self.shop_items.reload()
        await respond(msg, "**AFFIRMATIVE. Items reloaded.**")

# Events

    async def on_reaction_add(self, reaction, user):
        gid = str(reaction.message.guild.id)
        mid = reaction.message.id

        if reaction.emoji in self.Shop._emoji \
                and gid in self.shops \
                and mid in self.shops[gid] \
                and self.shops[gid][mid].user == user.id:
            await self.shops[gid][mid].react(reaction)
        if user.id != self.client.user.id:
            try:
                await reaction.message.remove_reaction(reaction.emoji, user)
            except (Forbidden, NotFound):
                pass

    async def on_global_tick(self, *_):
        if self._save_chars:
            self.chars.save()
            self._save_chars = False
        for guild_shops in self.shops.values():
            for mid in [k for k, v in guild_shops.items()
                        if time() - v.time > self.plugin_config.get('shop_delay', 120)]:
                await guild_shops[mid]._message.delete()
                del guild_shops[mid]
