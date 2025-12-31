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

# 1. Global Settings
# Columns: guild_id, manager_role_id, asst_role_id, contract_channel_id, free_agent_role_id, window_open
c.execute("""CREATE TABLE IF NOT EXISTS global_config (
             guild_id INTEGER PRIMARY KEY,
             manager_role_id INTEGER,
             asst_role_id INTEGER,
             contract_channel_id INTEGER,
             free_agent_role_id INTEGER,
             window_open INTEGER DEFAULT 1
             )""")

# --- DATABASE MIGRATION (Safe Update for existing DB) ---
try:
    c.execute("ALTER TABLE global_config ADD COLUMN free_agent_role_id INTEGER")
except sqlite3.OperationalError:
    pass # Column likely exists
try:
    c.execute("ALTER TABLE global_config ADD COLUMN window_open INTEGER DEFAULT 1")
except sqlite3.OperationalError:
    pass # Column likely exists

# 2. Teams Table
c.execute("""CREATE TABLE IF NOT EXISTS teams (
             team_role_id INTEGER PRIMARY KEY,
             logo TEXT,
             roster_limit INTEGER
             )""")

# 3. Free Agents Table (NEW)
c.execute("""CREATE TABLE IF NOT EXISTS free_agents (
             user_id INTEGER PRIMARY KEY,
             region TEXT,
             position TEXT,
             description TEXT,
             timestamp TEXT
             )""")
conn.commit()

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
    # Returns: (team_role_obj, logo, limit) or None
    for role in member.roles:
        data = get_team_data(role.id)
        if data:
            return (role, data[1], data[2]) 
    return None

def is_staff(interaction: discord.Interaction):
    return interaction.user.guild_permissions.administrator

def is_window_open(guild_id):
    config = get_global_config(guild_id)
    if not config: return True # Default to open if not set
    # Check column index 5 (window_open)
    try:
        return config[5] == 1
    except IndexError:
        return True # Fallback

def get_managers_of_team(guild, team_role):
    config = get_global_config(guild.id)
    if not config: return []
    
    mgr_id = config[1]
    asst_id = config[2]
    
    managers = []
    for member in team_role.members:
        r_ids = [r.id for r in member.roles]
        if mgr_id in r_ids or asst_id in r_ids:
            managers.append(member)
    return managers

async def cleanup_free_agent(guild, member):
    """Removes player from DB and removes FA role"""
    # 1. Remove from DB
    c.execute("DELETE FROM free_agents WHERE user_id = ?", (member.id,))
    conn.commit()
    
    # 2. Remove Role
    config = get_global_config(guild.id)
    if config and config[4]: # index 4 is free_agent_role_id
        role = guild.get_role(config[4])
        if role and role in member.roles:
            try:
                await member.remove_roles(role)
            except:
                pass

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

async def send_dm(user, content=None, embed=None, view=None):
    try:
        await user.send(content=content, embed=embed, view=view)
        return True
    except:
        return False

# --- TRANSFER VIEW (BUTTONS) ---
class TransferView(discord.ui.View):
    def __init__(self, guild, player, from_team, to_team, to_manager, logo):
        super().__init__(timeout=86400)
        self.guild = guild
        self.player = player
        self.from_team = from_team
        self.to_team = to_team
        self.to_manager = to_manager
        self.logo = logo

    @discord.ui.button(label="Accept Transfer", style=discord.ButtonStyle.green, emoji="✅")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Check Window Status inside the button too
        if not is_window_open(self.guild.id):
             return await interaction.response.send_message("❌ **Transfer Window is CLOSED.** Transaction cancelled.", ephemeral=True)

        try:
            member = self.guild.get_member(self.player.id)
            if not member:
                return await interaction.response.send_message("❌ Player not found in server anymore.", ephemeral=True)
            
            # Remove old, Add new
            await member.remove_roles(self.from_team)
            await member.add_roles(self.to_team)
            
            # Cleanup FA status if exists (just in case)
            await cleanup_free_agent(self.guild, member)

            # Announcement
            data = get_team_data(self.to_team.id)
            limit = data[2] if data else 0

            desc = f"🚨 **TRANSFER NEWS** 🚨\n\n{member.mention} has been transferred\nFrom: {self.from_team.mention}\nTo: {self.to_team.mention}"
            embed = create_transaction_embed(self.guild, "Official Transfer", desc, discord.Color.purple(), self.to_team, self.logo, self.to_manager, len(self.to_team.members), limit)
            
            await send_to_channel(self.guild, embed)
            await send_dm(self.to_manager, f"✅ Your transfer request for **{member.name}** was ACCEPTED!")
            
            self.stop()
            await interaction.response.send_message("✅ Transfer Accepted & Processed.")
            
            for child in self.children:
                child.disabled = True
            await interaction.message.edit(view=self)

        except Exception as e:
            await interaction.response.send_message(f"❌ Error processing transfer: {e}", ephemeral=True)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await send_dm(self.to_manager, f"❌ Your transfer request for **{self.player.name}** was DECLINED.")
        
        self.stop()
        await interaction.response.send_message("❌ Transfer Declined.")
        
        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)

# --- BOT CLASS ---
class LeagueBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.all())
        self.tree = app_commands.CommandTree(self)

    async def on_ready(self):
        await self.tree.sync()
        print(f"✅ LOGGED IN AS: {self.user}")

client = LeagueBot()

# --- COMMANDS ---

# UPDATED SETUP COMMAND
@client.tree.command(name="setup_global", description="Set roles and channels. Run this to update configs.")
async def setup_global(interaction: discord.Interaction, manager_role: discord.Role, asst_role: discord.Role, free_agent_role: discord.Role, channel: discord.TextChannel):
    if not is_staff(interaction): return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    
    # We use Replace to update. Note: window_open defaults to 1 (Open) if not touched, but we want to preserve it if it exists.
    # To keep it simple, we check if a config exists first to preserve window state.
    
    current_config = get_global_config(interaction.guild.id)
    window_state = 1
    if current_config and len(current_config) > 5:
        window_state = current_config[5]
    
    # Insert or Replace
    c.execute("INSERT OR REPLACE INTO global_config VALUES (?, ?, ?, ?, ?, ?)", 
              (interaction.guild.id, manager_role.id, asst_role.id, channel.id, free_agent_role.id, window_state))
    conn.commit()
    await interaction.response.send_message(f"✅ **Global Config Saved!**\nManager: {manager_role.mention}\nFA Role: {free_agent_role.mention}", ephemeral=True)

@client.tree.command(name="setup_team", description="Register a Team Role")
async def setup_team(interaction: discord.Interaction, team_role: discord.Role, logo: str, roster_limit: int = 20):
    if not is_staff(interaction): return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    c.execute("INSERT OR REPLACE INTO teams VALUES (?, ?, ?)", (team_role.id, logo, roster_limit))
    conn.commit()
    await interaction.response.send_message(f"✅ **{team_role.name}** registered!", ephemeral=True)

@client.tree.command(name="team_delete", description="Unregister a team from the bot (Admin Only)")
async def team_delete(interaction: discord.Interaction, team_role: discord.Role):
    if not is_staff(interaction): return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    c.execute("DELETE FROM teams WHERE team_role_id = ?", (team_role.id,))
    conn.commit()
    await interaction.response.send_message(f"🗑️ **{team_role.name}** removed.", ephemeral=True)

# --- NEW: TRANSFER WINDOW COMMAND ---
@client.tree.command(name="window", description="Open or Close the Transfer Window")
@app_commands.choices(status=[
    app_commands.Choice(name="Open ✅", value=1),
    app_commands.Choice(name="Closed ❌", value=0)
])
async def window(interaction: discord.Interaction, status: int):
    if not is_staff(interaction): return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    
    # Update only the window column
    c.execute("UPDATE global_config SET window_open = ? WHERE guild_id = ?", (status, interaction.guild.id))
    conn.commit()
    
    msg = "✅ **Transfer Window is now OPEN!** Teams may sign and release players." if status == 1 else "❌ **Transfer Window is now CLOSED!** Transactions are frozen."
    await interaction.response.send_message(msg)
    
    # Optional: Announce to contract channel
    conf = get_global_config(interaction.guild.id)
    if conf and conf[3]:
        chan = interaction.guild.get_channel(conf[3])
        if chan: await chan.send(msg)

# --- NEW: HELP COMMAND ---
@client.tree.command(name="help", description="Show all available commands and features")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(title="📚 League Bot Help Guide", color=discord.Color.brand_green())
    
    # Admin Section
    admin_text = (
        "**/setup_global**\n"
        "Configure Global Manager, Assistant, FA Roles, and Transaction Channel.\n"
        "**/window [open/close]**\n"
        "Control the Transfer Window. If CLOSED, no transactions can occur.\n"
        "**/setup_team**\n"
        "Register a Team Role, Logo, and Roster Limit.\n"
        "**/team_list**\n"
        "View all registered teams and rosters."
    )
    embed.add_field(name="🛠️ Admin Commands", value=admin_text, inline=False)

    # Manager Section
    manager_text = (
        "**/free_agents**\n"
        "View players looking for a team (Region/Position/Desc).\n"
        "**/sign [player]**\n"
        "Sign a player. Auto-removes them from Free Agency.\n"
        "**/release [player]**\n"
        "Remove a player from your team.\n"
        "**/transfer [player]**\n"
        "Request to buy a player from another team via DM negotiation."
    )
    embed.add_field(name="📢 Manager Commands", value=manager_text, inline=False)

    # Player Section
    player_text = (
        "**/looking_for_team**\n"
        "List yourself as a Free Agent (Region/Position/Desc).\n"
        "**/demand**\n"
        "Leave your current team (Alerts managers)."
    )
    embed.add_field(name="⚽ Player Commands", value=player_text, inline=False)
    
    # System Info
    system_text = (
        "• **Transfer Window:** Checks status before any sign/release/transfer.\n"
        "• **Auto-Cleanup:** Signing a player removes FA status automatically.\n"
        "• **Negotiations:** Transfers require opposing manager approval via DM."
    )
    embed.add_field(name="⚙️ System Features", value=system_text, inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)

# --- FREE AGENCY COMMANDS ---

@client.tree.command(name="looking_for_team", description="Post yourself as a Free Agent")
@app_commands.choices(region=[
    app_commands.Choice(name="Asia", value="ASIA"),
    app_commands.Choice(name="Europe", value="EU"),
    app_commands.Choice(name="North America", value="NA"),
    app_commands.Choice(name="South America", value="SA"),
    app_commands.Choice(name="Oceania", value="OCE")
], position=[
    app_commands.Choice(name="Striker (ST)", value="ST"),
    app_commands.Choice(name="Midfielder (MF)", value="MF"),
    app_commands.Choice(name="Defender (DF)", value="DF"),
    app_commands.Choice(name="Goalkeeper (GK)", value="GK")
])
async def looking_for_team(interaction: discord.Interaction, region: str, position: str, description: str):
    # 1. Update DB
    c.execute("INSERT OR REPLACE INTO free_agents VALUES (?, ?, ?, ?, ?)", 
              (interaction.user.id, region, position, description, str(datetime.datetime.now())))
    conn.commit()
    
    # 2. Give Role
    config = get_global_config(interaction.guild.id)
    if config and config[4]: # FA Role ID
        role = interaction.guild.get_role(config[4])
        if role:
            await interaction.user.add_roles(role)
    
    await interaction.response.send_message(f"✅ You are now listed as a **Free Agent** ({region} - {position})!", ephemeral=True)

@client.tree.command(name="free_agents", description="View list of available players")
async def free_agents(interaction: discord.Interaction):
    await interaction.response.defer()
    
    c.execute("SELECT * FROM free_agents")
    agents = c.fetchall()
    
    if not agents:
        return await interaction.followup.send("🤷‍♂️ No Free Agents currently listed.")
        
    embed = discord.Embed(title="📄 Free Agency Market", color=discord.Color.teal())
    
    count = 0
    for agent in agents:
        # id, region, pos, desc, time
        uid, reg, pos, desc, _ = agent
        member = interaction.guild.get_member(uid)
        
        if member:
            embed.add_field(
                name=f"{pos} | {member.name} ({reg})",
                value=f"📝 {desc}",
                inline=False
            )
            count += 1
            if count >= 20: # Limit to prevent errors
                embed.set_footer(text="Showing first 20 agents...")
                break
                
    await interaction.followup.send(embed=embed)

# --- TRANSACTION COMMANDS ---

@client.tree.command(name="sign", description="Sign a player to YOUR team")
async def sign(interaction: discord.Interaction, player: discord.Member):
    # CHECK WINDOW
    if not is_window_open(interaction.guild.id):
        return await interaction.response.send_message("❌ **The Transfer Window is CLOSED.**", ephemeral=True)

    g_config = get_global_config(interaction.guild.id)
    if not g_config: return await interaction.response.send_message("❌ Run `/setup_global` first!", ephemeral=True)
    
    # Auth Check
    user_roles = [r.id for r in interaction.user.roles]
    if (g_config[1] not in user_roles) and (g_config[2] not in user_roles):
        return await interaction.response.send_message("❌ Not Authorized.", ephemeral=True)
    
    team_info = find_user_team(interaction.user)
    if not team_info: return await interaction.response.send_message("❌ You have no team role.", ephemeral=True)
    team_role, logo, limit = team_info 

    if team_role in player.roles:
        return await interaction.response.send_message("⚠️ Player is already on this team.", ephemeral=True)

    existing_team = find_user_team(player)
    if existing_team:
        other_team_role = existing_team[0]
        return await interaction.response.send_message(f"🚫 **Illegal Move:** Player is on **{other_team_role.name}**. Use `/transfer`.", ephemeral=True)
    
    if len(team_role.members) >= limit:
        return await interaction.response.send_message("❌ Roster Full!", ephemeral=True)
    
    await player.add_roles(team_role)
    
    # CLEANUP FREE AGENT
    await cleanup_free_agent(interaction.guild, player)
    
    desc = f"The {team_role.mention} have **signed** {player.mention}"
    embed = create_transaction_embed(interaction.guild, f"{team_role.name} Transaction", desc, discord.Color.blue(), team_role, logo, interaction.user, len(team_role.members), limit)
    
    sent = await send_to_channel(interaction.guild, embed)
    await send_dm(player, content=f"✅ You have been signed to **{team_role.name}**!", embed=embed)

    if sent: await interaction.response.send_message("✅ Signed!", ephemeral=True)
    else: await interaction.response.send_message(embed=embed)

@client.tree.command(name="release", description="Release a player")
async def release(interaction: discord.Interaction, player: discord.Member):
    # CHECK WINDOW
    if not is_window_open(interaction.guild.id):
        return await interaction.response.send_message("❌ **The Transfer Window is CLOSED.**", ephemeral=True)

    g_config = get_global_config(interaction.guild.id)
    if not g_config: return await interaction.response.send_message("❌ Run setup first", ephemeral=True)
    
    user_roles = [r.id for r in interaction.user.roles]
    if (g_config[1] not in user_roles) and (g_config[2] not in user_roles):
        return await interaction.response.send_message("❌ Not Authorized.", ephemeral=True)

    team_info = find_user_team(interaction.user)
    if not team_info: return await interaction.response.send_message("❌ You have no team.", ephemeral=True)
    team_role, logo, limit = team_info
    
    if team_role not in player.roles:
        return await interaction.response.send_message("⚠️ Player not on your team.", ephemeral=True)
        
    await player.remove_roles(team_role)
    
    # Note: We do NOT automatically add them to Free Agents DB/Role upon release. 
    # They must run /looking_for_team themselves.
    
    desc = f"The **{team_role.name}** have **released** {player.mention}"
    embed = create_transaction_embed(interaction.guild, f"{team_role.name} Transaction", desc, discord.Color.red(), team_role, logo, interaction.user, len(team_role.members), limit)
    
    sent = await send_to_channel(interaction.guild, embed)
    await send_dm(player, content=f"⚠️ You have been released from **{team_role.name}**.", embed=embed)
    
    if sent: await interaction.response.send_message("✅ Released!", ephemeral=True)
    else: await interaction.response.send_message(embed=embed)

@client.tree.command(name="demand", description="Leave your current team")
async def demand(interaction: discord.Interaction):
    # Note: Players can usually Demand Transfer even if window is closed (internal team drama), 
    # but they can't be signed by a NEW team until window opens. 
    # If you want to block this too, uncomment the next two lines:
    # if not is_window_open(interaction.guild.id):
    #    return await interaction.response.send_message("❌ **The Transfer Window is CLOSED.**", ephemeral=True)

    team_info = find_user_team(interaction.user)
    if not team_info: return await interaction.response.send_message("❌ Not in a team.", ephemeral=True)
    team_role, logo, limit = team_info
    
    await interaction.user.remove_roles(team_role)
    
    desc = f"{interaction.user.mention} has **Demanded Transfer** from {team_role.mention}"
    embed = create_transaction_embed(interaction.guild, "Transfer Demand", desc, discord.Color.dark_grey(), team_role, logo, None, len(team_role.members), limit)
    
    sent = await send_to_channel(interaction.guild, embed)
    managers = get_managers_of_team(interaction.guild, team_role)
    for mgr in managers:
        await send_dm(mgr, content=f"📢 **Alert:** {interaction.user.name} has left your team **{team_role.name}**.")

    if sent: await interaction.response.send_message(f"👋 Left **{team_role.name}**.", ephemeral=True)
    else: await interaction.response.send_message(embed=embed)

@client.tree.command(name="team_list", description="List teams (Admin)")
async def team_list(interaction: discord.Interaction):
    if not is_staff(interaction): return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    await interaction.response.defer()
    
    all_teams = get_all_teams()
    if not all_teams: return await interaction.followup.send("❌ No teams.")

    embed = discord.Embed(title="🏆 Registered Teams List", color=discord.Color.gold())
    for t_data in all_teams:
        role_id, logo, _ = t_data
        team_role = interaction.guild.get_role(role_id)
        if not team_role: continue

        header_emoji = logo if (logo and "http" not in logo) else "🛡️"
        players = [m.mention for m in team_role.members]
        player_str = "\n".join(players) if players else "*No players.*"
        
        embed.add_field(name=f"{header_emoji} {team_role.name} ({len(players)})", value=player_str, inline=False)

    await interaction.followup.send(embed=embed)

@client.tree.command(name="transfer", description="Request to sign a player from another team")
async def transfer(interaction: discord.Interaction, player: discord.Member):
    # CHECK WINDOW
    if not is_window_open(interaction.guild.id):
        return await interaction.response.send_message("❌ **The Transfer Window is CLOSED.**", ephemeral=True)

    my_team_info = find_user_team(interaction.user)
    if not my_team_info:
        return await interaction.response.send_message("❌ You are not managing a registered team.", ephemeral=True)
    my_team_role, my_logo, _ = my_team_info

    target_team_info = find_user_team(player)
    if not target_team_info:
        return await interaction.response.send_message(f"⚠️ {player.name} is not on a registered team. Use `/sign` instead.", ephemeral=True)
    target_team_role, _, _ = target_team_info

    if my_team_role.id == target_team_role.id:
        return await interaction.response.send_message("⚠️ That player is already on your team!", ephemeral=True)

    opposing_managers = get_managers_of_team(interaction.guild, target_team_role)
    if not opposing_managers:
        return await interaction.response.send_message(f"❌ **{target_team_role.name}** has no active Manager/Assistant to approve this.", ephemeral=True)

    target_manager = opposing_managers[0] 
    
    view = TransferView(interaction.guild, player, target_team_role, my_team_role, interaction.user, my_logo)
    
    dm_embed = discord.Embed(title="Transfer Offer 📝", color=discord.Color.gold())
    dm_embed.description = (
        f"**{interaction.user.mention}** (Manager of {my_team_role.name}) wants to buy **{player.name}** from your team.\n\n"
        f"Do you accept this transfer?"
    )
    
    success = await send_dm(target_manager, embed=dm_embed, view=view)
    
    if success:
        await interaction.response.send_message(f"✅ **Offer Sent!** Waiting for {target_manager.mention} to respond.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Could not DM the opposing manager ({target_manager.name}). Transfer failed.", ephemeral=True)

# --- STARTUP ---
print("System: Loading Logic V7 (Free Agency + Windows)...")
if TOKEN:
    try:
        keep_alive()
        client.run(TOKEN)
    except Exception as e:
        print(f"❌ Error: {e}")Error: {e}")