import discord
from discord import app_commands
from discord.ui import View, Button
import asyncio
import re
import os
import json
import time
import random
import string
from dotenv import load_dotenv

# ─────────────────────────────────────────────
# Setup
# ─────────────────────────────────────────────
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
SAVE_FILE = "sessions.json"

# ─────────────────────────────────────────────
# Time Helpers
# ─────────────────────────────────────────────
def hm(h, m):
    return h * 3600 + m * 60

def fmt(seconds):
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h}:{m:02d}"

# ─────────────────────────────────────────────
# Spawn Times
# ─────────────────────────────────────────────
RIFT_SPAWNS = [
    hm(1,30), hm(3,0), hm(4,30), hm(6,0), hm(7,30), hm(9,0),
    hm(10,30), hm(12,0), hm(13,30), hm(15,0), hm(16,30), hm(18,0),
    hm(19,30), hm(21,0), hm(22,30), hm(24,0), hm(25,30), hm(27,0),
    hm(28,30), hm(30,0), hm(31,30), hm(33,0), hm(34,30), hm(36,0),
    hm(37,30), hm(39,0), hm(40,30), hm(42,0), hm(43,30), hm(45,0),
    hm(46,30),
]

BOSS_SPAWNS = [
    hm(2,0), hm(4,0), hm(6,0), hm(8,0), hm(10,0), hm(12,0),
    hm(14,0), hm(16,0), hm(18,0), hm(20,0), hm(22,0), hm(24,0),
    hm(26,0), hm(28,0), hm(30,0), hm(32,0), hm(34,0), hm(36,0),
    hm(38,0), hm(40,0), hm(42,0), hm(44,0), hm(46,0),
]

NOTIFY_BEFORE = 5 * 60
active_tasks = {}

# ─────────────────────────────────────────────
# Utility
# ─────────────────────────────────────────────
def generate_id(existing):
    while True:
        sid = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        if sid not in existing:
            return sid

def normalize_link(link):
    link = link.strip()
    if re.match(r"^ropro\.io/invite/[A-Za-z0-9]+$", link):
        return "https://" + link
    return link

def find_role(guild, name):
    role = discord.utils.find(lambda r: r.name.lower() == name.lower(), guild.roles)
    return role.mention if role else f"@{name}"

# ─────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────
def load_sessions():
    if not os.path.exists(SAVE_FILE):
        return {}
    try:
        with open(SAVE_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_sessions(data):
    with open(SAVE_FILE, "w") as f:
        json.dump(data, f, indent=2)

def save_session(session_id, data):
    sessions = load_sessions()
    sessions[session_id] = data
    save_sessions(sessions)

def delete_session(session_id):
    sessions = load_sessions()
    if session_id in sessions:
        del sessions[session_id]
        save_sessions(sessions)

# ─────────────────────────────────────────────
# UI
# ─────────────────────────────────────────────
class JoinView(View):
    def __init__(self, link):
        super().__init__(timeout=None)
        self.add_item(Button(
            label="Join Server",
            url=link,
            style=discord.ButtonStyle.link,
            emoji="🔗"
        ))

# ─────────────────────────────────────────────
# Notifications
# ─────────────────────────────────────────────
async def send_warning(channel, delay, spawn_time, spawn_type, guild, session_id, link):
    try:
        if delay > 0:
            await asyncio.sleep(delay)

        role_ping = find_role(guild, spawn_type)
        is_rift = spawn_type == "rift"

        embed = discord.Embed(
            title=f"{'🌀' if is_rift else '👹'} {spawn_type.upper()} in 5 minutes!",
            description=(
                f"{role_ping} **{spawn_type.upper()}** spawns in **5 minutes**!\n"
                f"Server time: `{fmt(spawn_time)}`"
            ),
            color=discord.Color.blurple() if is_rift else discord.Color.red()
        )

        embed.set_footer(text=f"Session {session_id}")

        await channel.send(
            content=role_ping,
            embed=embed,
            view=JoinView(link)
        )
    except asyncio.CancelledError:
        pass

# ─────────────────────────────────────────────
# Scheduler
# ─────────────────────────────────────────────
def cancel_tasks(session_id):
    tasks = active_tasks.pop(session_id, [])
    for t in tasks:
        t.cancel()
    return len(tasks)

def schedule_session(session_id, guild, channel, start_unix, link):
    now = int(time.time() - start_unix)
    cancel_tasks(session_id)

    tasks = []
    skipped = 0

    def schedule(spawns, label):
        nonlocal skipped
        count = 0

        for s in spawns:
            if s <= now:
                skipped += 1
                continue

            delay = max(0, s - now - NOTIFY_BEFORE)

            task = asyncio.create_task(
                send_warning(channel, delay, s, label, guild, session_id, link)
            )
            tasks.append(task)
            count += 1

        return count

    rift_count = schedule(RIFT_SPAWNS, "rift")
    boss_count = schedule(BOSS_SPAWNS, "boss")

    active_tasks[session_id] = tasks
    return rift_count, boss_count, skipped

# ─────────────────────────────────────────────
# Bot
# ─────────────────────────────────────────────
class SpawnBot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()

    async def on_ready(self):
        print(f"Logged in as {self.user}")
        await self.restore_sessions()

    async def restore_sessions(self):
        sessions = load_sessions()
        for sid, data in sessions.items():
            guild = self.get_guild(data["guild_id"])
            channel = self.get_channel(data["channel_id"])

            if guild and channel:
                schedule_session(
                    sid,
                    guild,
                    channel,
                    data["server_start_unix"],
                    data["link"]
                )

bot = SpawnBot()

# ─────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────
@bot.tree.command(name="stime", description="Set server time and invite link")
@app_commands.describe(current_time="Server time in H:MM", link="RoPro invite link")
async def stime(interaction: discord.Interaction, current_time: str, link: str):
    match = re.match(r"^(\d+):(\d{2})$", current_time.strip())
    if not match:
        await interaction.response.send_message("❌ Use H:MM format.", ephemeral=True)
        return

    h, m = int(match.group(1)), int(match.group(2))
    if m >= 60:
        await interaction.response.send_message("❌ Invalid minutes.", ephemeral=True)
        return

    uptime = hm(h, m)
    start_unix = time.time() - uptime
    link = normalize_link(link)

    session_id = generate_id(set(load_sessions().keys()))

    rift, boss, skipped = schedule_session(
        session_id,
        interaction.guild,
        interaction.channel,
        start_unix,
        link
    )

    save_session(session_id, {
        "guild_id": interaction.guild.id,
        "channel_id": interaction.channel.id,
        "server_start_unix": start_unix,
        "label": current_time,
        "link": link
    })

    embed = discord.Embed(title="✅ Session Created", color=discord.Color.green())
    embed.add_field(name="Session ID", value=f"`{session_id}`", inline=False)
    embed.add_field(name="Server Time", value=f"`{current_time}`", inline=True)
    embed.add_field(name="Link", value=link, inline=False)
    embed.add_field(name="🌀 Rift", value=str(rift), inline=True)
    embed.add_field(name="👹 Boss", value=str(boss), inline=True)
    embed.add_field(name="⏭️ Skipped", value=str(skipped), inline=True)
    embed.set_footer(text=f"Requested by {interaction.user}")

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="allserver", description="List sessions")
async def allserver(interaction: discord.Interaction):
    sessions = load_sessions()

    embed = discord.Embed(title="📋 Active Sessions", color=discord.Color.blurple())
    found = False

    for sid, data in sessions.items():
        if data["guild_id"] != interaction.guild.id:
            continue

        found = True

        now_unix = int(time.time())
        uptime = now_unix - int(data["server_start_unix"])

        next_rift = next((s for s in RIFT_SPAWNS if s > uptime), None)
        next_boss = next((s for s in BOSS_SPAWNS if s > uptime), None)

        def format_ts(target):
            if target is None:
                return "Finished"
            spawn_unix = int(data["server_start_unix"] + target)
            return f"<t:{spawn_unix}:R>"

        embed.add_field(
            name=f"Session `{sid}`",
            value=(
                f"🕒 `{data['label']}`\n"
                f"🔗 {data['link']}\n"
                f"🌀 Rift: {format_ts(next_rift)}\n"
                f"👹 Boss: {format_ts(next_boss)}"
            ),
            inline=False
        )

    if not found:
        embed.description = "No active sessions."

    embed.set_footer(text=f"Requested by {interaction.user}")

    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="cancel", description="Cancel a session")
async def cancel(interaction: discord.Interaction, session_id: str):
    removed = cancel_tasks(session_id)
    delete_session(session_id)

    await interaction.response.send_message(
        f"✅ Cancelled `{session_id}` ({removed} tasks).",
        ephemeral=True
    )

@bot.tree.command(name="cancelall", description="Cancel all sessions")
async def cancelall(interaction: discord.Interaction):
    sessions = load_sessions()
    removed = 0

    for sid, data in list(sessions.items()):
        if data["guild_id"] == interaction.guild.id:
            cancel_tasks(sid)
            delete_session(sid)
            removed += 1

    await interaction.response.send_message(
        f"✅ Cancelled {removed} session(s).",
        ephemeral=True
    )

# ─────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────
bot.run(TOKEN)