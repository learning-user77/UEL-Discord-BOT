import discord
from discord import app_commands
import sqlite3
import datetime
import os
from keep_alive import keep_alive

# --- IMPORTS FOR IMAGE GENERATION ---
from PIL import Image, ImageDraw, ImageFont
import io
import aiohttp

# --- CONFIGURATION ---
TOKEN = os.environ.get('TOKEN')
DEFAULT_BG_FILE = "proxima_default.jpg" # Rename your uploaded banner to this!

# --- DATABASE SETUP ---
conn = sqlite3.connect('team_manager.db')
c = conn.cursor()

# 1. Global Settings
c.execute("""CREATE TABLE IF NOT EXISTS global_config (
             guild_id INTEGER PRIMARY KEY,
             manager_role_id INTEGER,
             asst_role_id INTEGER,
             contract_channel_id INTEGER,
             free_agent_role_id INTEGER,
             window_open INTEGER DEFAULT 1
             )""")

# 2. Teams Table
c.execute("""CREATE TABLE IF NOT EXISTS teams (
             team_role_id INTEGER PRIMARY KEY,
             logo TEXT,
             roster_limit INTEGER,
             transaction_image TEXT
             )""")

# Migrations (Auto-fix for older DB versions)
try: c.execute("ALTER TABLE global_config ADD COLUMN free_agent_role_id INTEGER")
except sqlite3.OperationalError: pass 
try: c.execute("ALTER TABLE global_config ADD COLUMN window_open INTEGER DEFAULT 1")
except sqlite3.OperationalError: pass
try: c.execute("ALTER TABLE teams ADD COLUMN transaction_image TEXT")
except sqlite3.OperationalError: pass

# 3. Free Agents
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
    for role in member.roles:
        data = get_team_data(role.id)
        if data:
            trans_img = data[3] if len(data) > 3 else None
            return (role, data[1], data[2], trans_img) 
    return None

def is_staff(interaction: discord.Interaction):
    return interaction.user.guild_permissions.administrator

def is_window_open(guild_id):
    config = get_global_config(guild_id)
    if not config: return True 
    try: return config[5] == 1
    except IndexError: return True 

def get_managers_of_team(guild, team_role):
    config = get_global_config(guild.id)
    if not config: return ([], [])
    mgr_id, asst_id = config[1], config[2]
    head_managers, assistants = [], []
    for member in team_role.members:
        r_ids = [r.id for r in member.roles]
        if mgr_id in r_ids: head_managers.append(member)
        elif asst_id in r_ids: assistants.append(member)
    return (head_managers, assistants)

async def cleanup_free_agent(guild, member):
    c.execute("DELETE FROM free_agents WHERE user_id = ?", (member.id,))
    conn.commit()
    config = get_global_config(guild.id)
    if config and config[4]: 
        role = guild.get_role(config[4])
        if role and role in member.roles:
            try: await member.remove_roles(role)
            except: pass

def format_roster_list(members, mgr_id, asst_id):
    formatted_list = []
    for m in members:
        r_ids = [r.id for r in m.roles]
        name = m.mention
        if mgr_id in r_ids: name += " **(TM)**"
        elif asst_id in r_ids: name += " **(AM)**"
        formatted_list.append(name)
    return formatted_list

# --- MASTER CARD GENERATOR ---
async def generate_transaction_card(player, team_name, team_color, title_text="OFFICIAL SIGNING", custom_bg_url=None):
    """
    Generates a transaction card.
    Priority: Custom Team URL > Local Default File (Proxima Logo) > Solid Color
    """
    
    W, H = 800, 400
    img = None
    
    # 1. Background Priority: Custom URL (from /decorate_transactions)
    if custom_bg_url:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(custom_bg_url) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        bg_img = Image.open(io.BytesIO(data)).convert("RGB")
                        img = bg_img.resize((W, H))
                        
                        # Add Dark Overlay for readability
                        overlay = Image.new("RGBA", (W, H), (0,0,0,0))
                        draw_overlay = ImageDraw.Draw(overlay)
                        draw_overlay.rectangle([(0, 240), (W, H)], fill=(0, 0, 0, 160))
                        img.paste(overlay, (0,0), mask=overlay)
        except Exception as e:
            print(f"Failed to load custom bg: {e}")
            img = None

    # 2. Background Priority: Local Default File (Your Uploaded Image)
    if img is None:
        if os.path.exists(DEFAULT_BG_FILE):
            try:
                bg_img = Image.open(DEFAULT_BG_FILE).convert("RGB")
                img = bg_img.resize((W, H))
                # Add slight overlay to ensure text pops
                overlay = Image.new("RGBA", (W, H), (0,0,0,0))
                draw_overlay = ImageDraw.Draw(overlay)
                draw_overlay.rectangle([(0, 240), (W, H)], fill=(0, 0, 0, 140))
                img.paste(overlay, (0,0), mask=overlay)
            except Exception as e:
                print(f"Failed to load local default: {e}")

    # 3. Background Priority: Solid Color (Fallback)
    if img is None:
        bg_color = team_color.to_rgb()
        if bg_color == (0, 0, 0): bg_color = (44, 47, 51)
        img = Image.new("RGB", (W, H), color=bg_color)

    draw = ImageDraw.Draw(img)

    # 4. Avatar Handling
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(player.display_avatar.url) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    avatar = Image.open(io.BytesIO(data)).convert("RGBA")
                    avatar = avatar.resize((200, 200))
                    
                    # Circular Mask
                    mask = Image.new("L", (200, 200), 0)
                    draw_mask = ImageDraw.Draw(mask)
                    draw_mask.ellipse((0, 0, 200, 200), fill=255)
                    
                    img.paste(avatar, (300, 50), mask=mask)
                    draw.ellipse((300, 50, 500, 250), outline="white", width=3)
    except:
        pass 

    # 5. Text Handling (Using font.ttf)
    try:
        font_large = ImageFont.truetype("font.ttf", 60)
        font_small = ImageFont.truetype("font.ttf", 40)
    except OSError:
        print("⚠️ 'font.ttf' not found. Using default.")
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    draw.text((W/2, 290), title_text, fill="white", font=font_small, anchor="mm")
    draw.text((W/2, 350), player.name.upper(), fill="white", font=font_large, anchor="mm")
    
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    
    return discord.File(buffer, filename="transaction.png")

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

async def send_to_channel(guild, embed, file=None):
    config = get_global_config(guild.id)
    if config and config[3]: 
        channel = guild.get_channel(config[3])
        if channel:
            await channel.send(embed=embed, file=file)
            return True
    return False

async def send_dm(user, content=None, embed=None, view=None):
    try: await user.send(content=content, embed=embed, view=view); return True
    except: return False

# --- TRANSFER VIEW ---
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
        if not is_window_open(self.guild.id):
             return await interaction.response.send_message("❌ **Transfer Window is CLOSED.**", ephemeral=True)

        try:
            member = self.guild.get_member(self.player.id)
            if not member: return await interaction.response.send_message("❌ Player missing.", ephemeral=True)
            
            await member.remove_roles(self.from_team)
            await member.add_roles(self.to_team)
            await cleanup_free_agent(self.guild, member)

            desc = f"🚨 **TRANSFER NEWS** 🚨\n\n{member.mention} has been transferred\nFrom: {self.from_team.mention}\nTo: {self.to_team.mention}"
            
            data = get_team_data(self.to_team.id)
            limit = data[2] if data else 0
            custom_bg = data[3] if data and len(data) > 3 else None

            embed = create_transaction_embed(self.guild, "Official Transfer", desc, discord.Color.purple(), self.to_team, self.logo, self.to_manager, len(self.to_team.members), limit)
            
            # Transfer Card
            file = await generate_transaction_card(member, self.to_team.name, self.to_team.color, "OFFICIAL TRANSFER", custom_bg)
            embed.set_image(url="attachment://transaction.png")

            await send_to_channel(self.guild, embed, file)
            await send_dm(self.to_manager, f"✅ Transfer for **{member.name}** ACCEPTED!")
            
            self.stop()
            await interaction.response.send_message("✅ Processed.")
            for child in self.children: child.disabled = True
            await interaction.message.edit(view=self)

        except Exception as e:
            await interaction.response.send_message(f"❌ Error: {e}", ephemeral=True)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red, emoji="❌")
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        await send_dm(self.to_manager, f"❌ Transfer for **{self.player.name}** DECLINED.")
        self.stop()
        await interaction.response.send_message("❌ Declined.")
        for child in self.children: child.disabled = True
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

@client.tree.command(name="setup_global", description="Set roles and channels.")
async def setup_global(interaction: discord.Interaction, manager_role: discord.Role, asst_role: discord.Role, free_agent_role: discord.Role, channel: discord.TextChannel):
    if not is_staff(interaction): return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    current_config = get_global_config(interaction.guild.id)
    window_state = 1
    if current_config and len(current_config) > 5: window_state = current_config[5]
    c.execute("INSERT OR REPLACE INTO global_config VALUES (?, ?, ?, ?, ?, ?)", 
              (interaction.guild.id, manager_role.id, asst_role.id, channel.id, free_agent_role.id, window_state))
    conn.commit()
    await interaction.response.send_message(f"✅ **Config Saved!**", ephemeral=True)

@client.tree.command(name="setup_team", description="Register a Team Role")
async def setup_team(interaction: discord.Interaction, team_role: discord.Role, logo: str, roster_limit: int = 20):
    if not is_staff(interaction): return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    existing = get_team_data(team_role.id)
    trans_img = existing[3] if existing and len(existing) > 3 else None
    c.execute("INSERT OR REPLACE INTO teams VALUES (?, ?, ?, ?)", (team_role.id, logo, roster_limit, trans_img))
    conn.commit()
    await interaction.response.send_message(f"✅ **{team_role.name}** registered!", ephemeral=True)

@client.tree.command(name="team_delete", description="Unregister a team")
async def team_delete(interaction: discord.Interaction, team_role: discord.Role):
    if not is_staff(interaction): return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    c.execute("DELETE FROM teams WHERE team_role_id = ?", (team_role.id,))
    conn.commit()
    await interaction.response.send_message(f"🗑️ **{team_role.name}** removed.", ephemeral=True)

@client.tree.command(name="window", description="Open/Close Window")
@app_commands.choices(status=[app_commands.Choice(name="Open ✅", value=1), app_commands.Choice(name="Closed ❌", value=0)])
async def window(interaction: discord.Interaction, status: int):
    if not is_staff(interaction): return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    c.execute("UPDATE global_config SET window_open = ? WHERE guild_id = ?", (status, interaction.guild.id))
    conn.commit()
    msg = "✅ **Transfer Window OPEN!**" if status == 1 else "❌ **Transfer Window CLOSED!**"
    await interaction.response.send_message(msg)
    conf = get_global_config(interaction.guild.id)
    if conf and conf[3]:
        chan = interaction.guild.get_channel(conf[3])
        if chan: await chan.send(msg)

@client.tree.command(name="decorate_transactions", description="Set custom contract background (Upload Image OR Link)")
async def decorate_transactions(interaction: discord.Interaction, image_file: discord.Attachment = None, url: str = None):
    # 1. Permissions
    g_config = get_global_config(interaction.guild.id)
    user_roles = [r.id for r in interaction.user.roles]
    if (g_config[1] not in user_roles) and (g_config[2] not in user_roles) and not interaction.user.guild_permissions.administrator:
        return await interaction.response.send_message("❌ Managers or Admins only.", ephemeral=True)

    # 2. Find Team
    team_info = find_user_team(interaction.user)
    if not team_info: return await interaction.response.send_message("❌ You aren't managing a team.", ephemeral=True)
    team_role, _, _, _ = team_info

    # 3. Determine Input
    final_url = None
    
    if url and url.lower() in ["reset", "none", "remove"]:
        c.execute("UPDATE teams SET transaction_image = NULL WHERE team_role_id = ?", (team_role.id,))
        conn.commit()
        return await interaction.response.send_message(f"✅ **{team_role.name}** reverted to Proxima Default.")

    if image_file:
        if not image_file.content_type.startswith("image/"):
             return await interaction.response.send_message("❌ File must be an image.", ephemeral=True)
        final_url = image_file.url
    elif url:
        if not url.startswith("http"):
            return await interaction.response.send_message("❌ Invalid Link.", ephemeral=True)
        final_url = url
    else:
        return await interaction.response.send_message("❌ Provide an **Image File** OR a **URL**.", ephemeral=True)

    # 4. Save to DB
    c.execute("UPDATE teams SET transaction_image = ? WHERE team_role_id = ?", (final_url, team_role.id))
    conn.commit()

    # 5. Preview
    embed = discord.Embed(title="Background Updated", description="Your future signings will look like this:", color=discord.Color.green())
    embed.set_image(url=final_url)
    await interaction.response.send_message(f"✅ **{team_role.name}** custom background set!", embed=embed, ephemeral=True)

@client.tree.command(name="sign", description="Sign a player to YOUR team")
async def sign(interaction: discord.Interaction, player: discord.Member):
    await interaction.response.defer()
    if not is_window_open(interaction.guild.id): return await interaction.followup.send("❌ **Window Closed.**")

    g_config = get_global_config(interaction.guild.id)
    user_roles = [r.id for r in interaction.user.roles]
    if (g_config[1] not in user_roles) and (g_config[2] not in user_roles): return await interaction.followup.send("❌ Not Authorized.")
    
    team_info = find_user_team(interaction.user)
    if not team_info: return await interaction.followup.send("❌ No team role.")
    team_role, logo, limit, custom_bg = team_info 

    if team_role in player.roles: return await interaction.followup.send("⚠️ Already on team.")
    if find_user_team(player): return await interaction.followup.send(f"🚫 Player on another team. Use `/transfer`.")
    if len(team_role.members) >= limit: return await interaction.followup.send("❌ Roster Full!")
    
    await player.add_roles(team_role)
    await cleanup_free_agent(interaction.guild, player)
    
    desc = f"The {team_role.mention} have **signed** {player.mention}"
    embed = create_transaction_embed(interaction.guild, f"{team_role.name} Transaction", desc, discord.Color.blue(), team_role, logo, interaction.user, len(team_role.members), limit)

    # SIGNING CARD
    try:
        file = await generate_transaction_card(player, team_role.name, team_role.color, "OFFICIAL SIGNING", custom_bg)
        embed.set_image(url="attachment://transaction.png")
        await send_to_channel(interaction.guild, embed, file)
    except Exception as e:
        await interaction.followup.send(f"Error generating card: {e}")
        await send_to_channel(interaction.guild, embed)

    await send_dm(player, content=f"✅ Signed to **{team_role.name}**!", embed=embed)
    await interaction.followup.send("✅ Player Signed!")

@client.tree.command(name="release", description="Release a player")
async def release(interaction: discord.Interaction, player: discord.Member):
    if not is_window_open(interaction.guild.id): return await interaction.response.send_message("❌ Window Closed.", ephemeral=True)
    
    team_info = find_user_team(interaction.user)
    if not team_info: return await interaction.response.send_message("❌ No team.", ephemeral=True)
    team_role, logo, limit, custom_bg = team_info
    
    if team_role not in player.roles: return await interaction.response.send_message("⚠️ Player not on team.", ephemeral=True)
    await player.remove_roles(team_role)
    
    desc = f"The **{team_role.name}** have **released** {player.mention}"
    embed = create_transaction_embed(interaction.guild, f"{team_role.name} Transaction", desc, discord.Color.red(), team_role, logo, interaction.user, len(team_role.members), limit)
    
    # RELEASE CARD
    try:
        file = await generate_transaction_card(player, team_role.name, team_role.color, "OFFICIAL RELEASE", custom_bg)
        embed.set_image(url="attachment://transaction.png")
        await send_to_channel(interaction.guild, embed, file)
    except:
        await send_to_channel(interaction.guild, embed)

    await send_dm(player, content=f"⚠️ Released from **{team_role.name}**.", embed=embed)
    await interaction.response.send_message("✅ Released!", ephemeral=True)

@client.tree.command(name="demand", description="Leave your current team")
async def demand(interaction: discord.Interaction):
    team_info = find_user_team(interaction.user)
    if not team_info: return await interaction.response.send_message("❌ Not in a team.", ephemeral=True)
    team_role, logo, limit, _ = team_info
    
    await interaction.user.remove_roles(team_role)
    config = get_global_config(interaction.guild.id)
    if config and config[4]: 
        fa_role = interaction.guild.get_role(config[4])
        if fa_role: await interaction.user.add_roles(fa_role)

    desc = f"{interaction.user.mention} has **Demanded Release** from the team."
    embed = create_transaction_embed(interaction.guild, "Transfer Demand", desc, discord.Color.dark_grey(), team_role, logo, None, len(team_role.members), limit)
    await send_to_channel(interaction.guild, embed)
    
    heads, assts = get_managers_of_team(interaction.guild, team_role)
    for mgr in heads + assts: await send_dm(mgr, content=f"📢 {interaction.user.name} has left your team.")
    await interaction.response.send_message(f"👋 Left **{team_role.name}**.", ephemeral=True)

@client.tree.command(name="looking_for_team", description="Post yourself as a Free Agent")
@app_commands.choices(region=[app_commands.Choice(name="Asia", value="ASIA"), app_commands.Choice(name="Europe", value="EU"), app_commands.Choice(name="NA", value="NA"), app_commands.Choice(name="SA", value="SA")], 
                      position=[app_commands.Choice(name="ST", value="ST"), app_commands.Choice(name="MF", value="MF"), app_commands.Choice(name="DF", value="DF"), app_commands.Choice(name="GK", value="GK")])
async def looking_for_team(interaction: discord.Interaction, region: str, position: str, description: str):
    c.execute("INSERT OR REPLACE INTO free_agents VALUES (?, ?, ?, ?, ?)", (interaction.user.id, region, position, description, str(datetime.datetime.now())))
    conn.commit()
    config = get_global_config(interaction.guild.id)
    if config and config[4]: 
        role = interaction.guild.get_role(config[4])
        if role: await interaction.user.add_roles(role)
    await interaction.response.send_message(f"✅ Listed as **Free Agent** ({region} - {position})!", ephemeral=True)

@client.tree.command(name="free_agents", description="View available players")
async def free_agents(interaction: discord.Interaction):
    await interaction.response.defer()
    c.execute("SELECT * FROM free_agents")
    agents = c.fetchall()
    if not agents: return await interaction.followup.send("🤷‍♂️ No Free Agents currently listed.")
    embed = discord.Embed(title="📄 Free Agency Market", color=discord.Color.teal())
    count = 0
    for agent in agents:
        uid, reg, pos, desc, _ = agent
        member = interaction.guild.get_member(uid)
        if member:
            embed.add_field(name=f"{pos} | {member.name} ({reg})", value=f"📝 {desc}", inline=False)
            count += 1
            if count >= 20: 
                embed.set_footer(text="Showing first 20 agents...")
                break
    await interaction.followup.send(embed=embed)

@client.tree.command(name="team_list", description="List teams (Admin)")
async def team_list(interaction: discord.Interaction):
    if not is_staff(interaction): return await interaction.response.send_message("❌ Admin Only", ephemeral=True)
    await interaction.response.defer()
    
    g_conf = get_global_config(interaction.guild.id)
    mgr_id = g_conf[1] if g_conf else 0
    asst_id = g_conf[2] if g_conf else 0
    
    all_teams = get_all_teams()
    if not all_teams: return await interaction.followup.send("❌ No teams.")
    embed = discord.Embed(title="🏆 Registered Teams List", color=discord.Color.gold())
    for t_data in all_teams:
        role_id = t_data[0]
        logo = t_data[1]
        team_role = interaction.guild.get_role(role_id)
        if not team_role: continue
        header_emoji = logo if (logo and "http" not in logo) else "🛡️"
        members_formatted = format_roster_list(team_role.members, mgr_id, asst_id)
        player_str = "\n".join(members_formatted) if members_formatted else "*No players.*"
        embed.add_field(name=f"{header_emoji} {team_role.name} ({len(team_role.members)})", value=player_str, inline=False)
    await interaction.followup.send(embed=embed)

@client.tree.command(name="team_view", description="View a specific team's roster")
async def team_view(interaction: discord.Interaction, team: discord.Role):
    data = get_team_data(team.id)
    if not data: return await interaction.response.send_message("❌ Not a registered team.", ephemeral=True)
    g_conf = get_global_config(interaction.guild.id)
    mgr_id = g_conf[1] if g_conf else 0
    asst_id = g_conf[2] if g_conf else 0
    logo = data[1]
    header_emoji = logo if (logo and "http" not in logo) else "🛡️"
    members_formatted = format_roster_list(team.members, mgr_id, asst_id)
    player_str = "\n".join(members_formatted) if members_formatted else "*No players.*"
    embed = discord.Embed(title=f"{header_emoji} {team.name} Roster", color=team.color)
    if logo and "http" in logo: embed.set_thumbnail(url=logo)
    embed.description = player_str
    embed.set_footer(text=f"Total: {len(team.members)}")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@client.tree.command(name="transfer", description="Request to sign a player")
async def transfer(interaction: discord.Interaction, player: discord.Member):
    if not is_window_open(interaction.guild.id): return await interaction.response.send_message("❌ **Window CLOSED.**", ephemeral=True)
    my_team_info = find_user_team(interaction.user)
    if not my_team_info: return await interaction.response.send_message("❌ Not a manager.", ephemeral=True)
    my_team_role, my_logo, _, _ = my_team_info
    
    target_team_info = find_user_team(player)
    if not target_team_info: return await interaction.response.send_message("⚠️ Player not on a team.", ephemeral=True)
    target_team_role, _, _, _ = target_team_info
    
    if my_team_role.id == target_team_role.id: return await interaction.response.send_message("⚠️ Already on your team!", ephemeral=True)

    heads, assts = get_managers_of_team(interaction.guild, target_team_role)
    target_manager = heads[0] if heads else (assts[0] if assts else None)
    if not target_manager: return await interaction.response.send_message(f"❌ **{target_team_role.name}** has no active Manager.", ephemeral=True)

    view = TransferView(interaction.guild, player, target_team_role, my_team_role, interaction.user, my_logo)
    dm_embed = discord.Embed(title="Transfer Offer 📝", color=discord.Color.gold())
    dm_embed.description = f"**{interaction.user.mention}** wants to buy **{player.name}**.\nDo you accept?"
    
    if await send_dm(target_manager, embed=dm_embed, view=view):
        await interaction.response.send_message(f"✅ **Offer Sent!** Waiting for {target_manager.mention}.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ Could not DM manager.", ephemeral=True)

@client.tree.command(name="test_card", description="TEST: Generates a sample signing card")
async def test_card(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        color = interaction.user.top_role.color
        if color == discord.Color.default(): color = discord.Color.dark_grey()
        file = await generate_transaction_card(interaction.user, "Test Team", color, "TEST CARD")
        await interaction.followup.send("🖼️ **Test Image Generation:**", file=file)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {e}")

# --- STARTUP ---
print("System: Loading Proxima V14 (Font Fix & Logo Support)...")
if TOKEN:
    try:
        keep_alive()
        client.run(TOKEN)
    except Exception as e:
        print(f"❌ Error: {e}")
