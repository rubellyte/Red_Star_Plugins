from red_star.plugin_manager import BasePlugin
from red_star.command_dispatcher import Command
from red_star.rs_utils import respond, RSArgumentParser, split_message, find_role
from red_star.rs_errors import CommandSyntaxError, UserPermissionError
from copy import deepcopy
from discord import Member
import shlex


class RoleRequest(BasePlugin):
    name = "role_request"
    version = "1.1"
    author = "GTG3000"

    default_config = {
        "default": {
            "roles": [],
            "default_roles": []
        }
    }

    def _initialize(self, gid):
        if gid not in self.plugin_config:
            self.plugin_config[gid] = deepcopy(self.plugin_config['default'])

    async def on_member_join(self, member: Member):
        """
        Handles the call of on_member_join to apply the default roles, if any.
        """
        roles = self.plugin_config.get(str(member.guild.id), self.plugin_config['default'])['default_roles']
        if roles:
            roles = map(lambda rid: find_role(member.guild, str(rid)), roles)
            await member.add_roles(*roles, reason="Adding default roles.")

    @Command("ManageRequestableRoles", "MReqRoles",
             doc="-a/--add   : Adds specified roles to the list of allowed requestable roles.\n"
                 "-r/--remove: Removes speficied roles from the list.\n"
                 "Calling it without any arguments prints the list.",
             syntax="[-a/--add (role mentions/ids/names)] [-r/--remove (role mentions/ids/names)]",
             perms={"manage_roles"},
             category="role_request")
    async def _manage(self, msg):
        gid = str(msg.guild.id)
        self._initialize(gid)

        parser = RSArgumentParser()
        parser.add_argument("command")
        parser.add_argument("-a", "--add", default=[], nargs='+')
        parser.add_argument("-r", "--remove", default=[], nargs='+')

        args = parser.parse_args(shlex.split(msg.content))

        if not (args['add'] or args['remove']):
            role_str = "\n".join(x.name for x in msg.guild.roles if x.id in self.plugin_config[gid]["roles"])
            for split_msg in split_message(f"**ANALYSIS: Currently approved requestable roles:**```\n{role_str}```"):
                await respond(msg, split_msg)
        else:
            args['add'] = [r for r in [find_role(msg.guild, r) for r in args['add']] if r]
            args['remove'] = [r for r in [find_role(msg.guild, r) for r in args['remove']] if r]

            # for nice output
            added_roles = []
            removed_roles = []

            for role in args['add']:
                if role.id not in self.plugin_config[gid]["roles"]:
                    added_roles.append(role.name)
                    self.plugin_config[gid]["roles"].append(role.id)
            for role in args['remove']:
                if role.id in self.plugin_config[gid]["roles"]:
                    removed_roles.append(role.name)
                    self.plugin_config[gid]["roles"].remove(role.id)

            if added_roles or removed_roles:
                output_str = "**AFFIRMATIVE. ANALYSIS:**\n```diff\n"
                if added_roles:
                    output_str += "Added roles:\n+ " + "\n+ ".join(added_roles) + "\n"
                if removed_roles:
                    output_str += "Removed roles:\n- " + "\n- ".join(removed_roles) + "\n"
                output_str += "```"
                await respond(msg, output_str)
            else:
                raise CommandSyntaxError

    @Command("RequestRole",
             doc="Adds or removes the specified requestable role from the user.\n"
                 "Role can be specified by name or ID. Please don't mention roles.",
             syntax="(role)",
             category="role_request")
    async def _requestrole(self, msg):
        gid = str(msg.guild.id)

        try:
            query = msg.content.split(None, 1)[1]
        except IndexError:
            raise CommandSyntaxError("Role query required.")

        role = find_role(msg.guild, query)

        if not role:
            raise CommandSyntaxError(f"Unable to find role {query}.")
        elif role.id not in self.plugin_config[gid]['roles']:
            raise UserPermissionError(f"Role {role.name} is not requestable.")
        else:
            if role in msg.author.roles:
                rem = True
                await msg.author.remove_roles(role, reason="Removed by request through plugin.")
            else:
                rem = False
                await msg.author.add_roles(role, reason="Added by request through plugin.")
            await respond(msg, f"**AFFIRMATIVE. Role {role.name} {'removed' if rem else 'added'}.**")

    @Command("DefaultRole",
             doc="-a/--add   : Adds specified roles to the list of default roles.\n"
                 "-r/--remove: Removes speficied roles from the list.\n"
                 "Calling it without any arguments prints the list.",
             syntax="[-a/--add (role mentions/ids/names)] [-r/--remove (role mentions/ids/names)]",
             perms={"manage_roles"},
             category="role_request")
    async def _manage_default(self, msg):
        gid = str(msg.guild.id)
        self._initialize(gid)

        parser = RSArgumentParser()
        parser.add_argument("command")
        parser.add_argument("-a", "--add", default=[], nargs='+')
        parser.add_argument("-r", "--remove", default=[], nargs='+')

        args = parser.parse_args(shlex.split(msg.content))

        d_r_list = self.plugin_config[gid].setdefault("default_roles", [])

        if not (args['add'] or args['remove']):
            role_str = "\n".join(x.name for x in msg.guild.roles if x.id in d_r_list)
            for split_msg in split_message(f"**ANALYSIS: Currently approved default roles:```\n{role_str}```"):
                await respond(msg, split_msg)
        else:
            args['add'] = [r for r in [find_role(msg.guild, r) for r in args['add']] if r]
            args['remove'] = [r for r in [find_role(msg.guild, r) for r in args['remove']] if r]

            # for nice output
            added_roles = []
            removed_roles = []

            for role in args['add']:
                if role.id not in d_r_list:
                    added_roles.append(role.name)
                    d_r_list.append(role.id)
            for role in args['remove']:
                if role.id in d_r_list:
                    removed_roles.append(role.name)
                    d_r_list.remove(role.id)

            if added_roles or removed_roles:
                output_str = "**AFFIRMATIVE. ANALYSIS:**\n```diff\n"
                if added_roles:
                    output_str += "Added roles:\n+ " + "\n+ ".join(added_roles) + "\n"
                if removed_roles:
                    output_str += "Removed roles:\n- " + "\n- ".join(removed_roles) + "\n"
                output_str += "```"
                await respond(msg, output_str)
            else:
                raise CommandSyntaxError
