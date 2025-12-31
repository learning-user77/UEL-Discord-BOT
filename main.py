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
    
    # NEW: Send a welcome message when joining a server to guide setup
    async def on_guild_join(self, guild):
        msg = (
            "👋 **Thanks for adding me!**\n\n"
            "To get started, an Admin needs to run:\n"
            "1. `/setup_global` (To set Manager roles and Log channel)\n"
            "2. `/setup_team` (To register your specific Team Roles)"
        )
        if guild.system_channel:
            await guild.system_channel.send(msg)

client = LeagueBot()

# --- HELPER FUNCTIONS ---

def get_global_config(guild_id):
    c.execute("SELECT * FROM global_config WHERE guild_id = ?", (guild_id,))
    return c.fetchone()

def get_team_data(role_id):
    c.execute("SELECT * FROM teams WHERE team_role_id = ?", (role_id,))
    return c.fetchone()

def get_all_teams():
    c.execute("SELECT * FROM teams")
    return c.fetchall()

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
    embed.set_author(name=guild.name, icon_url=guild.icon.url if guild.icon else None)
    embed.title = title
    
    if logo and "http" in logo:
        embed.set_thumbnail(url=logo)

    if coach:
        embed.add_field(name="Coach:", value=f"👔 {coach.mention}", inline=False)
    
    roster_text = f"{roster_count}/{limit}" if limit > 0 else f"{roster_count} (No Limit)"
    embed.add_field(name="Roster:", value=f"👥 {roster_text}", inline=False)
    
    embed.set_footer(text="Official Transaction")
    return embed

async def send_to_channel(guild, embed):
    config = get_global_config(guild.id)
    if config and config[3]: 
        channel = guild.get_channel(config[3])
        if channel:
            await channel.send(embed=embed)
            return True
    return False

# NEW: Helper to send DM
async def send_dm(user, content=None, embed=None):
    try:
        await user.send(content=content, embed=embed)
    except discord.Forbidden:
        print(f"❌ Could not DM {user.name} (DMs closed)")
    except Exception as e:
        print(f"❌ Error sending DM: {e}")

# --- COMMANDS ---

@client.tree.command(name="setup_global", description="Set the Common Manager roles and Contract Channel")
@app_commands.describe(manager_role="The Global Team Manager Role", asst_role="The Global Asst Manager Role", channel="Where transaction logs go")
async def setup_global(interaction: discord.Interaction, manager_role: discord.Role, asst_role: discord.Role, channel: discord.TextChannel):
    if not is_staff(interaction): return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    
    c.execute("INSERT OR REPLACE INTO global_config VALUES (?, ?, ?, ?)", 
              (interaction.guild.id, manager_role.id, asst_role.id, channel.id))
    conn.commit()
    await interaction.response.send_message(f"✅ **Global Config Saved!**\nManagers: {manager_role.mention} & {asst_role.mention}\nChannel: {channel.mention}", ephemeral=True)

@client.tree.command(name="setup_team", description="Register a Team Role so Managers can use it")
@app_commands.describe(team_role="The Team Role (e.g. Barca)", logo="Link or Emoji", roster_limit="Default 20")
async def setup_team(interaction: discord.Interaction, team_role: discord.Role, logo: str, roster_limit: int = 20):
    if not is_staff(interaction): return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    
    c.execute("INSERT OR REPLACE INTO teams VALUES (?, ?, ?)", (team_role.id, logo, roster_limit))
    conn.commit()
    await interaction.response.send_message(f"✅ **{team_role.name}** registered successfully!", ephemeral=True)

# 3. SIGN PLAYER
@client.tree.command(name="sign", description="Sign a player to YOUR team")
async def sign(interaction: discord.Interaction, player: discord.Member):
    g_config = get_global_config(interaction.guild.id)
    if not g_config: return await interaction.response.send_message("❌ Run `/setup_global` first!", ephemeral=True)
    
    user_roles = [r.id for r in interaction.user.roles]
    if (g_config[1] not in user_roles) and (g_config[2] not in user_roles):
        return await interaction.response.send_message("❌ You do not have the Manager or Asst Manager role.", ephemeral=True)
    
    team_info = find_user_team(interaction.user)
    if not team_info:
        return await interaction.response.send_message("❌ You have the Manager role, but **no Team Role**! Ask Admin to give you the specific team role.", ephemeral=True)
    
    team_role, logo, limit = team_info 

    if team_role in player.roles:
        return await interaction.response.send_message("⚠️ Player is already on this team.", ephemeral=True)
    
    current_count = len(team_role.members)
    if current_count >= limit:
        return await interaction.response.send_message(f"❌ Roster Full! ({current_count}/{limit})", ephemeral=True)
    
    # Action
    await player.add_roles(team_role)
    
    team_emoji = logo if (logo and "http" not in logo) else "🛡️"
    
    description = f"The {team_emoji} {team_role.mention} have **signed** {player.mention}"
    embed = create_transaction_embed(interaction.guild, f"{team_role.name} Transaction", description, discord.Color.blue(), team_role, logo, interaction.user, current_count + 1, limit)
    
    sent = await send_to_channel(interaction.guild, embed)
    
    # NEW FEATURE: DM Player
    dm_content = f"✅ You have been officially **signed** to **{team_role.name}** in {interaction.guild.name}!"
    await send_dm(player, content=dm_content, embed=embed)

    if sent:
        await interaction.response.send_message("✅ Signed! Check the contract channel.", ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed)

# 4. RELEASE PLAYER
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

    # NEW FEATURE: DM Player
    dm_content = f"⚠️ You have been **released** from **{team_role.name}** in {interaction.guild.name}."
    await send_dm(player, content=dm_content, embed=embed)

    if sent:
        await interaction.response.send_message("✅ Released!", ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed)

# 5. DEMAND (With Manager DM)
@client.tree.command(name="demand", description="Leave your current team voluntarily")
async def demand(interaction: discord.Interaction):
    team_info = find_user_team(interaction.user)
    
    if not team_info:
        return await interaction.response.send_message("❌ You are not in a registered team.", ephemeral=True)
        
    team_role, logo, limit = team_info
    
    # Action
    await interaction.user.remove_roles(team_role)
    
    current_count = len(team_role.members) - 1
    if current_count < 0: current_count = 0
    
    description = f"{interaction.user.mention} has **Demanded Transfer** from {team_role.mention}"
    embed = create_transaction_embed(interaction.guild, "Transfer Demand", description, discord.Color.dark_grey(), team_role, logo, None, current_count, limit)
    
    sent = await send_to_channel(interaction.guild, embed)

    # NEW FEATURE: DM The Team Managers
    g_config = get_global_config(interaction.guild.id)
    if g_config:
        mgr_id = g_config[1]
        asst_id = g_config[2]
        
        # We need to find members who have the TEAM role AND (MGR role OR ASST role)
        # Note: Since the player just left, they aren't in team_role.members anymore, so we scan the remaining members
        for member in team_role.members:
            member_role_ids = [r.id for r in member.roles]
            if (mgr_id in member_role_ids) or (asst_id in member_role_ids):
                # This member is a manager of this team
                dm_msg = f"📢 **Alert:** {interaction.user.name} has used /demand to leave your team **{team_role.name}**."
                await send_dm(member, content=dm_msg)

    if sent:
        await interaction.response.send_message(f"👋 You have left **{team_role.name}**.", ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed)

# 6. NEW FEATURE: TEAM LIST
@client.tree.command(name="team_list", description="List all registered teams and their players (Admin Only)")
async def team_list(interaction: discord.Interaction):
    # Only Admin
    if not is_staff(interaction): 
        return await interaction.response.send_message("❌ Admin Only", ephemeral=True)

    await interaction.response.defer() # Might take a second

    all_teams = get_all_teams() # Returns list of (role_id, logo, limit)
    if not all_teams:
        return await interaction.followup.send("❌ No teams registered yet.")

    embed = discord.Embed(title="🏆 Registered Teams List", color=discord.Color.gold())
    
    for t_data in all_teams:
        role_id = t_data[0]
        logo = t_data[1]
        
        team_role = interaction.guild.get_role(role_id)
        if not team_role:
            continue # Role might have been deleted from discord, skip it

        # Determine Emoji/Logo display
        header_emoji = logo if (logo and "http" not in logo) else "🛡️"
        
        # Build Player List
        # Warning: If > 50 players, this string gets too long. Assuming standard roster sizes.
        players = [m.mention for m in team_role.members]
        
        if len(players) > 0:
            player_str = "\n".join(players)
        else:
            player_str = "*No players signed.*"

        # Add Field
        field_name = f"{header_emoji} {team_role.name} ({len(players)})"
        embed.add_field(name=field_name, value=player_str, inline=False) # Inline False ensures vertical list

    await interaction.followup.send(embed=embed)

# --- STARTUP ---
print("System: Loading Logic V5...")
if TOKEN:
    try:
        keep_alive()
        client.run(TOKEN)
    except Exception as e:
        print(f"❌ Error: {e}")