import discord
from discord import app_commands
from discord.ext import commands
import sqlite3
import datetime
import os
import sys
from keep_alive import keep_alive

# --- CONFIGURATION ---
# We get the token from Render's secret vault
TOKEN = os.environ.get('TOKEN')

# --- DATABASE SETUP ---
conn = sqlite3.connect('team_manager.db')
c = conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS guild_config (
             guild_id INTEGER PRIMARY KEY,
             manager_role_id INTEGER,
             asst_role_id INTEGER,
             team_role_id INTEGER,
             logo_text TEXT
             )""")
conn.commit()

# --- BOT SETUP ---
class TeamBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.all())
        self.tree = app_commands.CommandTree(self)

    async def on_ready(self):
        await self.tree.sync()
        print(f"✅ LOGGED IN AS: {self.user} (ID: {self.user.id})")
        print("✅ Slash commands synced!")

client = TeamBot()

# --- HELPER FUNCTIONS ---
def get_config(guild_id):
    c.execute("SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,))
    return c.fetchone()

def create_embed(title, description, color, logo):
    embed = discord.Embed(title=title, description=description, color=color, timestamp=datetime.datetime.now())
    if logo and "http" in logo: embed.set_thumbnail(url=logo)
    elif logo: embed.set_footer(text=f"Team: {logo}")
    return embed

# --- COMMANDS ---

@client.tree.command(name="setup")
@app_commands.checks.has_permissions(administrator=True)
async def setup(interaction: discord.Interaction, manager_role: discord.Role, asst_role: discord.Role, team_role: discord.Role, logo: str):
    c.execute("INSERT OR REPLACE INTO guild_config VALUES (?, ?, ?, ?, ?)", (interaction.guild.id, manager_role.id, asst_role.id, team_role.id, logo))
    conn.commit()
    await interaction.response.send_message(embed=create_embed("✅ Setup Complete", "Roles saved!", discord.Color.green(), logo))

@client.tree.command(name="sign")
async def sign(interaction: discord.Interaction, player: discord.Member):
    config = get_config(interaction.guild.id)
    if not config: return await interaction.response.send_message("❌ Run /setup first", ephemeral=True)
    if config[3] in [r.id for r in player.roles]: return await interaction.response.send_message("⚠️ Already signed", ephemeral=True)
    
    # Check permission
    user_roles = [r.id for r in interaction.user.roles]
    if config[1] not in user_roles and config[2] not in user_roles:
         return await interaction.response.send_message("❌ Not authorized", ephemeral=True)
    
    await player.add_roles(interaction.guild.get_role(config[3]))
    await interaction.response.send_message(embed=create_embed("✍️ Signed", f"Welcome {player.mention}", discord.Color.gold(), config[4]))

@client.tree.command(name="promote")
async def promote(interaction: discord.Interaction, player: discord.Member):
    config = get_config(interaction.guild.id)
    if not config: return
    if config[1] not in [r.id for r in interaction.user.roles]: return await interaction.response.send_message("❌ Managers only", ephemeral=True)
    await player.add_roles(interaction.guild.get_role(config[2]))
    await interaction.response.send_message(embed=create_embed("🌟 Promoted", f"{player.mention} is now Asst Manager", discord.Color.purple(), config[4]))

@client.tree.command(name="release")
async def release(interaction: discord.Interaction, player: discord.Member):
    config = get_config(interaction.guild.id)
    if not config: return
    if config[1] not in [r.id for r in interaction.user.roles] and config[2] not in [r.id for r in interaction.user.roles]:
         return await interaction.response.send_message("❌ Not authorized", ephemeral=True)
    await player.remove_roles(interaction.guild.get_role(config[3]))
    if config[2] in [r.id for r in player.roles]: await player.remove_roles(interaction.guild.get_role(config[2]))
    await interaction.response.send_message(embed=create_embed("👋 Released", f"{player.mention} removed.", discord.Color.red(), config[4]))

@client.tree.command(name="demand")
async def demand(interaction: discord.Interaction):
    config = get_config(interaction.guild.id)
    if not config: return
    if config[3] not in [r.id for r in interaction.user.roles]: return await interaction.response.send_message("❌ You aren't in a team", ephemeral=True)
    await interaction.user.remove_roles(interaction.guild.get_role(config[3]))
    await interaction.response.send_message(embed=create_embed("📤 Left Team", f"{interaction.user.mention} left.", discord.Color.dark_gray(), config[4]))


# --- DEBUG SYSTEM (STARTS HERE) ---
print("-----------------------------------")
print("SYSTEM: Code is loading...")

# 1. Check if the Token was found
if TOKEN is None or TOKEN == "":
    print("❌ CRITICAL ERROR: The 'TOKEN' environment variable is empty!")
    print("👉 Go to Render Dashboard -> Environment -> Add 'TOKEN'.")
else:
    # We print the first 5 chars to verify it loaded, but keep the rest secret
    print(f"✅ TOKEN FOUND. Starting with: {TOKEN[:5]}...")

# 2. Start the Fake Website (The Waking Mechanism)
print("SYSTEM: Starting Web Server...")
try:
    keep_alive()
    print("✅ Web Server Started.")
except Exception as e:
    print(f"❌ Web Server Failed: {e}")

# 3. Start the Bot
print("SYSTEM: Attempting to log into Discord...")
try:
    client.run(TOKEN)
except discord.LoginFailure:
    print("❌ LOGIN FAILED: The Token is invalid. You must Reset Token in Discord Developer Portal.")
except Exception as e:
    print(f"❌ BOT CRASHED: {e}")