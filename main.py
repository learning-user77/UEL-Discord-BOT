import discord
from discord import app_commands
import sqlite3
import datetime
import os
from keep_alive import keep_alive

# --- CONFIGURATION ---
TOKEN = os.environ.get('TOKEN')

# --- DATABASE SETUP ---
conn = sqlite3.connect('team_manager.db')
c = conn.cursor()

# 1. Global Settings (Manager Roles & Output Channel)
c.execute("""CREATE TABLE IF NOT EXISTS global_config (
             guild_id INTEGER PRIMARY KEY,
             manager_role_id INTEGER,
             asst_role_id INTEGER,
             contract_channel_id INTEGER
             )""")

# 2. Teams Table (Just the Role, Logo, and Limit)
c.execute("""CREATE TABLE IF NOT EXISTS teams (
             team_role_id INTEGER PRIMARY KEY,
             logo TEXT,
             roster_limit INTEGER
             )""")
conn.commit()

# --- BOT SETUP ---
class LeagueBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.all())
        self.tree = app_commands.CommandTree(self)

    async def on_ready(self):
        await self.tree.sync()
        print(f"✅ LOGGED IN AS: {self.user}")

client = LeagueBot()

# --- HELPER FUNCTIONS ---

def get_global_config(guild_id):
    c.execute("SELECT * FROM global_config WHERE guild_id = ?", (guild_id,))
    return c.fetchone()

def get_team_data(role_id):
    c.execute("SELECT * FROM teams WHERE team_role_id = ?", (role_id,))
    return c.fetchone()

def find_user_team(member):
    # Scans the user's roles to see if they have a 'Registered Team Role'
    # Returns: (team_role_obj, logo, limit) or None
    for role in member.roles:
        data = get_team_data(role.id)
        if data:
            return (role, data[1], data[2]) # Role Object, Logo, Limit
    return None

def is_staff(interaction: discord.Interaction):
    # Checks for Admin permissions
    return interaction.user.guild_permissions.administrator

# --- EMBED GENERATOR ---
def create_transaction_embed(guild, title, description, color, team_role, logo, coach, roster_count, limit):
    embed = discord.Embed(description=description, color=color, timestamp=datetime.datetime.now())
    
    # 1. Top: Server Name + Icon
    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    
    # 2. Title: "Barca UEFC Transaction"
    embed.title = title
    
    # 3. Logo Logic (Link vs Emoji)
    # If it's a link (http), put it as the thumbnail. If it's just text/emoji, we don't set thumbnail.
    if logo and "http" in logo:
        embed.set_thumbnail(url=logo)

    # 4. Fields
    if coach:
        embed.add_field(name="Coach:", value=f"👔 {coach.mention}", inline=False)
    
    # Roster Math
    roster_text = f"{roster_count}/{limit}" if limit > 0 else f"{roster_count} (No Limit)"
    embed.add_field(name="Roster:", value=f"👥 {roster_text}", inline=False)
    
    embed.set_footer(text="Official Transaction")
    return embed

async def send_to_channel(guild, embed):
    config = get_global_config(guild.id)
    if config and config[3]: # If a channel is set
        channel = guild.get_channel(config[3])
        if channel:
            await channel.send(embed=embed)
            return True
    return False

# --- COMMANDS ---

# 1. GLOBAL SETUP (Run Once)
@client.tree.command(name="setup_global", description="Set the Common Manager roles and Contract Channel")
@app_commands.describe(manager_role="The Global Team Manager Role", asst_role="The Global Asst Manager Role", channel="Where transaction logs go")
async def setup_global(interaction: discord.Interaction, manager_role: discord.Role, asst_role: discord.Role, channel: discord.TextChannel):
    if not is_staff(interaction): return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    
    c.execute("INSERT OR REPLACE INTO global_config VALUES (?, ?, ?, ?)", 
              (interaction.guild.id, manager_role.id, asst_role.id, channel.id))
    conn.commit()
    await interaction.response.send_message(f"✅ **Global Config Saved!**\nManagers: {manager_role.mention} & {asst_role.mention}\nChannel: {channel.mention}", ephemeral=True)

# 2. REGISTER TEAM (Run per Team)
@client.tree.command(name="setup_team", description="Register a Team Role so Managers can use it")
@app_commands.describe(team_role="The Team Role (e.g. Barca)", logo="Link or Emoji", roster_limit="Default 20")
async def setup_team(interaction: discord.Interaction, team_role: discord.Role, logo: str, roster_limit: int = 20):
    if not is_staff(interaction): return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    
    c.execute("INSERT OR REPLACE INTO teams VALUES (?, ?, ?)", (team_role.id, logo, roster_limit))
    conn.commit()
    await interaction.response.send_message(f"✅ **{team_role.name}** registered successfully!", ephemeral=True)

# 3. SIGN PLAYER (Manager/Asst Only)
@client.tree.command(name="sign", description="Sign a player to YOUR team")
async def sign(interaction: discord.Interaction, player: discord.Member):
    # A. Check Global Config
    g_config = get_global_config(interaction.guild.id)
    if not g_config: return await interaction.response.send_message("❌ Run `/setup_global` first!", ephemeral=True)
    
    # B. Check if User is a Manager
    user_roles = [r.id for r in interaction.user.roles]
    if (g_config[1] not in user_roles) and (g_config[2] not in user_roles):
        return await interaction.response.send_message("❌ You do not have the Manager or Asst Manager role.", ephemeral=True)
    
    # C. Find WHICH team they manage (The Intersection Logic)
    team_info = find_user_team(interaction.user)
    if not team_info:
        return await interaction.response.send_message("❌ You have the Manager role, but **no Team Role**! Ask Admin to give you the specific team role.", ephemeral=True)
    
    team_role, logo, limit = team_info # Unpack

    # D. Logic Checks
    if team_role in player.roles:
        return await interaction.response.send_message("⚠️ Player is already on this team.", ephemeral=True)
    
    current_count = len(team_role.members)
    if current_count >= limit:
        return await interaction.response.send_message(f"❌ Roster Full! ({current_count}/{limit})", ephemeral=True)
    
    # E. Action
    await player.add_roles(team_role)
    
    # Text Formatting (Emoji check)
    team_emoji = logo if (logo and "http" not in logo) else "🛡️"
    
    description = f"The {team_emoji} {team_role.mention} have **signed** {player.mention}"
    embed = create_transaction_embed(interaction.guild, f"{team_role.name} Transaction", description, discord.Color.blue(), team_role, logo, interaction.user, current_count + 1, limit)
    
    # Send to channel
    sent = await send_to_channel(interaction.guild, embed)
    if sent:
        await interaction.response.send_message("✅ Signed! Check the contract channel.", ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed) # Fallback if no channel set

# 4. RELEASE PLAYER (Manager/Asst Only)
@client.tree.command(name="release", description="Release a player from YOUR team")
async def release(interaction: discord.Interaction, player: discord.Member):
    g_config = get_global_config(interaction.guild.id)
    if not g_config: return await interaction.response.send_message("❌ Run `/setup_global` first!", ephemeral=True)

    user_roles = [r.id for r in interaction.user.roles]
    if (g_config[1] not in user_roles) and (g_config[2] not in user_roles):
        return await interaction.response.send_message("❌ Not Authorized.", ephemeral=True)
        
    team_info = find_user_team(interaction.user)
    if not team_info: return await interaction.response.send_message("❌ You don't have a Team Role.", ephemeral=True)
    
    team_role, logo, limit = team_info
    
    if team_role not in player.roles:
        return await interaction.response.send_message("⚠️ Player is not on your team.", ephemeral=True)
        
    await player.remove_roles(team_role)
    
    current_count = len(team_role.members) - 1
    if current_count < 0: current_count = 0
    
    description = f"The **{team_role.name}** have **released** {player.mention}"
    embed = create_transaction_embed(interaction.guild, f"{team_role.name} Transaction", description, discord.Color.red(), team_role, logo, interaction.user, current_count, limit)
    
    sent = await send_to_channel(interaction.guild, embed)
    if sent:
        await interaction.response.send_message("✅ Released!", ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed)

# 5. DEMAND (Any Player)
@client.tree.command(name="demand", description="Leave your current team voluntarily")
async def demand(interaction: discord.Interaction):
    # Find if user is in ANY registered team
    team_info = find_user_team(interaction.user)
    
    if not team_info:
        return await interaction.response.send_message("❌ You are not in a registered team.", ephemeral=True)
        
    team_role, logo, limit = team_info
    
    # Confirmation Modal or Check could go here, but doing direct for now
    await interaction.user.remove_roles(team_role)
    
    current_count = len(team_role.members) - 1
    if current_count < 0: current_count = 0
    
    description = f"{interaction.user.mention} has **Demanded Transfer** from {team_role.mention}"
    embed = create_transaction_embed(interaction.guild, "Transfer Demand", description, discord.Color.dark_grey(), team_role, logo, None, current_count, limit)
    
    # We set Coach to None because it's the player doing it
    
    sent = await send_to_channel(interaction.guild, embed)
    if sent:
        await interaction.response.send_message(f"👋 You have left **{team_role.name}**.", ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed)

# --- STARTUP ---
print("System: Loading Logic V4...")
if TOKEN:
    try:
        keep_alive()
        client.run(TOKEN)
    except Exception as e:
        print(f"❌ Error: {e}")