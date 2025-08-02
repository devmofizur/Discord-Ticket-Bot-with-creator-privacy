import discord
from discord.ext import commands
from discord import app_commands, Interaction, ButtonStyle
from discord.ui import View, Button
from dotenv import load_dotenv
import json, os
from keep_alive import keep_alive
import logging

# Load environment variables
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# Patch logger to prevent formatting error
discord_logger = logging.getLogger("discord.gateway")
class SafeFormatter(logging.Formatter):
    def format(self, record):
        try:
            return super().format(record)
        except TypeError:
            return f"[LogError] {record.msg}"
for handler in discord_logger.handlers:
    handler.setFormatter(SafeFormatter(handler.formatter._fmt))

# Intents
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
tree = bot.tree

# Config files
if not os.path.exists("config.json"):
    with open("config.json", "w") as f:
        json.dump({"support_role_id": None, "ticket_category_id": None}, f)

if not os.path.exists("ticket_counter.json"):
    with open("ticket_counter.json", "w") as f:
        json.dump({"count": 1}, f)

def load_config():
    with open("config.json") as f:
        return json.load(f)

def save_config(data):
    with open("config.json", "w") as f:
        json.dump(data, f)

def get_ticket_count():
    with open("ticket_counter.json") as f:
        return json.load(f)["count"]

def increment_ticket_count():
    with open("ticket_counter.json", "r+") as f:
        data = json.load(f)
        data["count"] += 1
        f.seek(0)
        json.dump(data, f)
        f.truncate()

@tree.command(name="setup", description="Set the support role")
@app_commands.describe(role="Support team role")
async def setup_command(interaction: Interaction, role: discord.Role):
    config = load_config()
    config["support_role_id"] = role.id
    save_config(config)
    await interaction.response.send_message(f"✅ Support role set as {role.mention}", ephemeral=True)

@tree.command(name="category", description="Set the category where tickets will be created")
@app_commands.describe(category="Select a category")
async def set_category(interaction: Interaction, category: discord.CategoryChannel):
    config = load_config()
    config["ticket_category_id"] = category.id
    save_config(config)
    await interaction.response.send_message(f"✅ Ticket category set to {category.name}.", ephemeral=True)

class TicketView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(Button(label="🎫 Create Ticket", custom_id="create_ticket", style=ButtonStyle.green))

class TicketControlView(View):
    def __init__(self, creator_id, support_role_id):
        super().__init__(timeout=None)
        self.creator_id = creator_id
        self.support_role_id = support_role_id
        self.add_item(Button(label="🔒 Close", custom_id="close_ticket", style=ButtonStyle.secondary))
        self.add_item(Button(label="🗑️ Delete", custom_id="delete_ticket", style=ButtonStyle.danger))

    async def interaction_check(self, interaction: Interaction) -> bool:
        config = load_config()
        support_role = interaction.guild.get_role(config["support_role_id"])
        if interaction.user.id == self.creator_id or (support_role and support_role in interaction.user.roles):
            return True
        await interaction.response.send_message("❌ You don't have permission to use this button.", ephemeral=True)
        return False

@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    await tree.sync()
    keep_alive()

@bot.event
async def on_interaction(interaction: Interaction):
    if interaction.type == discord.InteractionType.component and interaction.data["custom_id"] == "create_ticket":
        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("❌ This command can only be used in a server.", ephemeral=True)
            return

        config = load_config()
        support_role_id = config["support_role_id"]
        category_id = config.get("ticket_category_id")

        if support_role_id is None:
            await interaction.followup.send("❌ Setup not complete. Ask admin to run `/setup`.", ephemeral=True)
            return

        guild = interaction.guild
        support_role = guild.get_role(support_role_id)
        if not support_role:
            await interaction.followup.send("❌ Support role not found. Please run `/setup` again.", ephemeral=True)
            return

        count = get_ticket_count()
        ticket_name = f"ticket-{count:03}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            support_role: discord.PermissionOverwrite(read_messages=True, send_messages=True),
        }

        category = guild.get_channel(category_id) if category_id else None

        try:
            channel = await guild.create_text_channel(ticket_name, overwrites=overwrites, category=category)
            role = await guild.create_role(name=ticket_name)
            await interaction.user.add_roles(role)
            await channel.set_permissions(role, read_messages=True, send_messages=True)

            embed = discord.Embed(
                title="🎫 Support Ticket Created",
                description=f"Hello {interaction.user.mention}, please wait while our team contacts you.",
                color=0x00ff00)
            view = TicketControlView(interaction.user.id, support_role_id)
            await channel.send(embed=embed, view=view)

            await interaction.followup.send(f"✅ Created ticket: {channel.mention}", ephemeral=True)
            increment_ticket_count()
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to create channels or roles.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred: {str(e)}", ephemeral=True)

    elif interaction.type == discord.InteractionType.component:
        config = load_config()
        support_role_id = config["support_role_id"]
        support_role = interaction.guild.get_role(support_role_id)

        custom_id = interaction.data["custom_id"]

        if custom_id == "close_ticket":
            if not interaction.channel.name.startswith("ticket-"):
                await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
                return

            old_name = interaction.channel.name
            new_name = old_name.replace("ticket-", "closed-")
            await interaction.channel.edit(name=new_name)

            role = discord.utils.get(interaction.guild.roles, name=old_name)
            if role:
                await role.edit(name=new_name)
                await interaction.channel.set_permissions(role, read_messages=False)

            embed = discord.Embed(
                title="🔒 Ticket Closed",
                description="This ticket has been closed. Use Reopen to open it again or Delete to remove it permanently.",
                color=0xffa500)
            view = View()
            view.add_item(Button(label="🔓 Reopen", custom_id="reopen_ticket", style=ButtonStyle.success))
            view.add_item(Button(label="🗑️ Delete", custom_id="delete_ticket", style=ButtonStyle.danger))
            await interaction.channel.send(embed=embed, view=view)
            await interaction.response.send_message("🔒 Ticket closed.", ephemeral=True)

        elif custom_id == "reopen_ticket":
            if not interaction.channel.name.startswith("closed-"):
                await interaction.response.send_message("❌ This is not a closed ticket channel.", ephemeral=True)
                return

            old_name = interaction.channel.name
            new_name = old_name.replace("closed-", "ticket-")
            await interaction.channel.edit(name=new_name)

            role = discord.utils.get(interaction.guild.roles, name=old_name)
            if role:
                await role.edit(name=new_name)
            else:
                role = await interaction.guild.create_role(name=new_name)

            await interaction.channel.set_permissions(role, read_messages=True, send_messages=True)

            embed = discord.Embed(
                title="🔓 Ticket Reopened",
                description="This ticket has been reopened. Our support team will get back to you shortly.",
                color=0x00ff00)
            view = TicketControlView(interaction.user.id, support_role_id)
            await interaction.channel.send(embed=embed, view=view)
            await interaction.response.send_message("🔄 Ticket reopened.", ephemeral=True)

        elif custom_id == "delete_ticket":
            if support_role and support_role not in interaction.user.roles:
                await interaction.response.send_message("❌ Only support staff can delete tickets.", ephemeral=True)
                return

            if not (interaction.channel.name.startswith("ticket-") or interaction.channel.name.startswith("closed-")):
                await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
                return

            role = discord.utils.get(interaction.guild.roles, name=interaction.channel.name)
            if role:
                await role.delete()
            await interaction.channel.delete()

@tree.command(name="close", description="Close the ticket")
async def close(interaction: Interaction):
    if not interaction.channel.name.startswith("ticket-"):
        await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
        return
    role = discord.utils.get(interaction.guild.roles, name=interaction.channel.name)
    if role:
        await interaction.channel.set_permissions(role, overwrite=discord.PermissionOverwrite(read_messages=False))
    await interaction.response.send_message("🔒 Ticket closed.")

@tree.command(name="delete", description="Delete the ticket and role")
async def delete(interaction: Interaction):
    config = load_config()
    support_role_id = config["support_role_id"]
    support_role = interaction.guild.get_role(support_role_id)

    if not support_role or support_role not in interaction.user.roles:
        await interaction.response.send_message("❌ Only support staff can delete tickets.", ephemeral=True)
        return

    if not interaction.channel.name.startswith("ticket-") and not interaction.channel.name.startswith("closed-"):
        await interaction.response.send_message("❌ This is not a ticket channel.", ephemeral=True)
        return

    role = discord.utils.get(interaction.guild.roles, name=interaction.channel.name)
    if role:
        await role.delete()
    await interaction.channel.delete()

@tree.command(name="ticket-menu", description="Create the ticket menu")
async def ticket_menu(interaction: Interaction):
    if not interaction.guild:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    embed = discord.Embed(
        title="Hello visitor, create a ticket to tell us your needs",
        description="To create a ticket use the Create ticket button\n\nOur team will respond as soon as possible",
        color=0x2F3136)

    view = TicketView()
    await interaction.response.send_message(embed=embed, view=view)

bot.run(TOKEN)
