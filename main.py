import os
import time
import asyncio
import nest_asyncio
import yt_dlp
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
import config  # Importing variables from config.py

# Apply nested asyncio for safety
nest_asyncio.apply()

# Initialize Client
app = Client(
    "pro_bot",
    api_id=config.API_ID,
    api_hash=config.API_HASH,
    bot_token=config.BOT_TOKEN
)

# Global Dictionary to store user links temporarily
# In production, use a database (Redis/SQLite)
user_sessions = {}

# --- HELPER: PROGRESS BAR ---
async def progress_bar(current, total, status_msg, start_time):
    now = time.time()
    # Update every 3 seconds to avoid FloodWait
    if (now - start_time) < 3 and current < total:
        return

    percentage = current * 100 / total
    speed = current / (now - start_time) if (now - start_time) > 0 else 0
    eta = round((total - current) / speed) if speed > 0 else 0
    
    # Visual Bar: [■■■■■□□□□□]
    filled_length = int(percentage // 10)
    bar = '■' * filled_length + '□' * (10 - filled_length)
    
    try:
        await status_msg.edit_text(
            f"📤 **Uploading...**\n"
            f"[{bar}] {percentage:.1f}%\n"
            f"💾 {current/1024/1024:.1f}MB / {total/1024/1024:.1f}MB\n"
            f"🚀 {speed/1024/1024:.1f} MB/s | ⏳ ETA: {eta}s"
        )
    except Exception:
        pass

# --- STEP 1: RECEIVE LINK ---
@app.on_message(filters.text & ~filters.command(["start"]))
async def receive_link(client, message):
    url = message.text
    if "http" not in url:
        return 

    status_msg = await message.reply_text("🔎 **Analyzing Link...**")
    
    try:
        # Fast fetch of metadata
        with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
            info = ydl.extract_info(url, download=False)
            title = info.get('title', 'Video')
            duration = info.get('duration', 0)
            
            # Save link to session
            user_sessions[message.from_user.id] = url
            
            await status_msg.edit_text(
                f"🎬 **{title}**\n"
                f"⏱ Duration: {format_time(duration)}\n\n"
                "👇 **Select Format:**",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🎥 Video (High Quality)", callback_data="vid")],
                    [InlineKeyboardButton("🎵 Audio (MP3)", callback_data="aud")]
                ])
            )
    except Exception as e:
        await status_msg.edit_text(f"❌ Error: {str(e)}")

# --- STEP 2: HANDLE BUTTON CLICK ---
@app.on_callback_query()
async def button_click(client, callback_query):
    user_id = callback_query.from_user.id
    mode = callback_query.data
    
    # Retrieve URL from session
    url = user_sessions.get(user_id)
    if not url:
        await callback_query.answer("❌ Session expired. Send link again.")
        return

    await callback_query.message.edit_text("⏳ **Downloading...**")
    
    # Run Download
    loop = asyncio.get_running_loop()
    file_info = await loop.run_in_executor(None, download_engine, url, mode)

    if "error" in file_info:
        await callback_query.message.edit_text(f"❌ Download Failed: {file_info['error']}")
        return

    # Upload
    start_time = time.time()
    try:
        if mode == "vid":
            await client.send_video(
                chat_id=callback_query.message.chat.id,
                video=file_info['path'],
                caption=f"🎥 **{file_info['title']}**",
                thumb=file_info['thumb'],
                duration=file_info['duration'],
                supports_streaming=True,
                progress=progress_bar,
                progress_args=(callback_query.message, start_time)
            )
        else:
            await client.send_audio(
                chat_id=callback_query.message.chat.id,
                audio=file_info['path'],
                caption=f"🎵 **{file_info['title']}**",
                thumb=file_info['thumb'],
                duration=file_info['duration'],
                progress=progress_bar,
                progress_args=(callback_query.message, start_time)
            )
            
        await callback_query.message.delete()
        
    except Exception as e:
        await callback_query.message.edit_text(f"❌ Upload Error: {e}")
    
    # Clean up
    if os.path.exists(file_info['path']): os.remove(file_info['path'])
    if file_info['thumb'] and os.path.exists(file_info['thumb']): os.remove(file_info['thumb'])

# --- CORE ENGINE ---
def download_engine(url, mode):
    # Check for cookies
    cookie_file = 'cookies.txt' if os.path.exists('cookies.txt') else None
    
    ydl_opts = {
        'quiet': True,
        'cookiefile': cookie_file,
        'outtmpl': f'{config.DOWNLOAD_LOCATION}/%(id)s.%(ext)s',
        'writethumbnail': True,
        'ffmpeg_location': '/usr/bin/ffmpeg' # Adjust if on Windows
    }

    if mode == "vid":
        ydl_opts.update({
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
            'postprocessor_args': {'merger': ['-c', 'copy', '-movflags', 'faststart']}
        })
    else:
        ydl_opts.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            ext = 'mp3' if mode == 'aud' else 'mp4'
            filename = ydl.prepare_filename(info)
            final_path = os.path.splitext(filename)[0] + "." + ext
            
            # Find Thumbnail (.webp or .jpg)
            base_name = os.path.splitext(filename)[0]
            thumb_path = None
            for t_ext in ['.webp', '.jpg', '.png']:
                if os.path.exists(base_name + t_ext):
                    thumb_path = base_name + t_ext
                    break
            
            return {
                'path': final_path,
                'thumb': thumb_path,
                'title': info.get('title', 'Media'),
                'duration': info.get('duration', 0)
            }
    except Exception as e:
        return {'error': str(e)}

def format_time(seconds):
    if not seconds: return "00:00"
    m, s = divmod(seconds, 60)
    return f"{m:02d}:{s:02d}"

if __name__ == "__main__":
    if not os.path.exists(config.DOWNLOAD_LOCATION):
        os.makedirs(config.DOWNLOAD_LOCATION)
    print("🤖 Bot Started...")
    app.run()