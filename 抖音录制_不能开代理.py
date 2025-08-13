import tkinter as tk
from tkinter import messagebox, ttk
import customtkinter as ctk
import subprocess
import threading
import json
from pathlib import Path
import os
import time
import datetime
import streamlink
import random

# --- 全局配置 ---
CONFIG_DIR = Path("recorder_config")
STREAMERS_FILE = CONFIG_DIR / "streamers.json"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
RECORDING_PATH_BASE = Path("recordings")
CHROME_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
DEFAULT_FFMPEG_PARAMS = {"c:v": "copy", "c:a": "copy", "f": "mkv"}
FFMPEG_OPTIONS = {
    "video_codecs": ["copy", "libx264", "libx265", "h264_nvenc", "hevc_nvenc", "h264_amf", "hevc_amf"],
    "audio_codecs": ["copy", "aac", "mp3", "opus"],
    "presets": ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"],
    "formats": ["flv","mkv", "mp4", "ts"],
}

# --- 工具函数 ---
def ensure_app_dirs():
    CONFIG_DIR.mkdir(exist_ok=True)
    RECORDING_PATH_BASE.mkdir(exist_ok=True)
def load_json(file_path, default_data):
    if not file_path.exists(): save_json(file_path, default_data)
    try:
        with open(file_path, 'r', encoding='utf-8') as f: return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError): return default_data
def save_json(file_path, data):
    with open(file_path, 'w', encoding='utf-8') as f: json.dump(data, f, indent=4, ensure_ascii=False)

# --- 自定义添加主播对话框 ---
class AddStreamerDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent); self.title("添加新主播"); self.geometry("350x200"); self.transient(parent); self.grab_set(); self.result = None
        ctk.CTkLabel(self, text="请输入主播房间号:").pack(padx=20, pady=(20, 5))
        self.id_entry = ctk.CTkEntry(self, width=300); self.id_entry.pack(padx=20)
        ctk.CTkLabel(self, text="请输入备注名:").pack(padx=20, pady=(10, 5))
        self.remark_entry = ctk.CTkEntry(self, width=300); self.remark_entry.pack(padx=20)
        button_frame = ctk.CTkFrame(self, fg_color="transparent"); button_frame.pack(pady=20)
        ctk.CTkButton(button_frame, text="确定", command=self.on_ok).pack(side="left", padx=10)
        ctk.CTkButton(button_frame, text="取消", command=self.destroy).pack(side="left", padx=10)
    def on_ok(self):
        room_id, remark = self.id_entry.get().strip(), self.remark_entry.get().strip()
        if room_id and remark: self.result = {"id": room_id, "remark": remark}; self.destroy()
        else: messagebox.showwarning("提示", "房间号和备注名不能为空。", parent=self)

# --- 主应用程序类 ---
class DouyinRecorderApp(ctk.CTk):
    def __init__(self):
        super().__init__(); self.title("抖音直播录制器 (V7 - 终极反屏蔽版)"); self.geometry("1400x800"); ensure_app_dirs()
        self.settings = load_json(SETTINGS_FILE, {"patrol_start": "20:00", "patrol_end": "02:00"})
        self.streamers = load_json(STREAMERS_FILE, {}); self.recording_threads = {}; self.patrol_thread = None
        self.patrol_active = threading.Event(); self.streamer_frames = {}; self.selected_room_id = None
        self.ffmpeg_setting_widgets = {}; self.crf_var = tk.StringVar(value="23"); self.patrol_status_var = tk.StringVar(value="巡逻已停止")
        self.create_widgets(); self.redraw_streamer_list(); self.protocol("WM_DELETE_WINDOW", self.on_closing); self.update_ui_states_periodically()

    def create_widgets(self):
        self.grid_columnconfigure(0, weight=2); self.grid_columnconfigure(1, weight=3); self.grid_rowconfigure(0, weight=1)
        self.left_panel = ctk.CTkFrame(self); self.left_panel.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        self.left_panel.grid_rowconfigure(2, weight=1)
        control_frame = ctk.CTkFrame(self.left_panel); control_frame.grid(row=0, column=0, pady=10, padx=10, sticky="ew")
        ctk.CTkButton(control_frame, text="➕ 添加主播", command=self.add_streamer).pack(side="left", padx=5)
        self.patrol_button = ctk.CTkButton(control_frame, text="▶️ 开启巡逻", command=self.toggle_patrol, fg_color="green"); self.patrol_button.pack(side="left", padx=5)
        patrol_time_frame = ctk.CTkFrame(control_frame); patrol_time_frame.pack(side="left", padx=10)
        ctk.CTkLabel(patrol_time_frame, text="巡逻时间:").pack(side="left", padx=5)
        self.patrol_start_entry = ctk.CTkEntry(patrol_time_frame, width=60); self.patrol_start_entry.pack(side="left"); self.patrol_start_entry.insert(0, self.settings.get("patrol_start", "20:00"))
        ctk.CTkLabel(patrol_time_frame, text="-").pack(side="left", padx=5)
        self.patrol_end_entry = ctk.CTkEntry(patrol_time_frame, width=60); self.patrol_end_entry.pack(side="left"); self.patrol_end_entry.insert(0, self.settings.get("patrol_end", "02:00"))
        ctk.CTkLabel(patrol_time_frame, textvariable=self.patrol_status_var).pack(side="left", padx=10)
        self.streamer_scroll_frame = ctk.CTkScrollableFrame(self.left_panel, label_text="主播列表"); self.streamer_scroll_frame.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)
        self.right_panel = ctk.CTkFrame(self); self.right_panel.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        self.right_panel.grid_rowconfigure(0, weight=1); self.right_panel.grid_columnconfigure(0, weight=1)
        self.tab_view = ctk.CTkTabview(self.right_panel); self.tab_view.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.tab_view.add("录制历史"); self.tab_view.add("FFmpeg 参数设置"); self.create_history_tab(); self.create_ffmpeg_settings_tab()
    
    def create_history_tab(self):
        history_tab = self.tab_view.tab("录制历史"); history_tab.grid_columnconfigure(0, weight=1); history_tab.grid_rowconfigure(0, weight=1)
        self.history_tree = ttk.Treeview(history_tab, columns=("filename", "start", "end", "size"), show="headings")
        self.history_tree.heading("filename", text="文件名"); self.history_tree.heading("start", text="开始时间"); self.history_tree.heading("end", text="结束时间"); self.history_tree.heading("size", text="文件大小")
        self.history_tree.grid(row=0, column=0, columnspan=3, sticky="nsew", padx=10, pady=10)
        button_frame = ctk.CTkFrame(history_tab); button_frame.grid(row=1, column=0, columnspan=3, pady=10)
        ctk.CTkButton(button_frame, text="▶️ 播放选中视频", command=self.play_history_video).pack(side="left", padx=5)
        ctk.CTkButton(button_frame, text="📂 打开文件夹", command=self.open_history_folder).pack(side="left", padx=5)
        ctk.CTkButton(button_frame, text="🗑️ 删除选中视频", command=self.delete_history_video).pack(side="left", padx=5)

    def create_ffmpeg_settings_tab(self):
        settings_tab = self.tab_view.tab("FFmpeg 参数设置"); settings_tab.grid_columnconfigure((0, 1), weight=1)
        video_frame = ctk.CTkFrame(settings_tab, border_width=1); video_frame.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")
        video_frame.grid_columnconfigure(1, weight=1); ctk.CTkLabel(video_frame, text="视频设置", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, columnspan=2, pady=5)
        ctk.CTkLabel(video_frame, text="视频编码器:").grid(row=1, column=0, padx=10, pady=5, sticky="w"); self.ffmpeg_setting_widgets["c:v"] = ctk.CTkOptionMenu(video_frame, values=FFMPEG_OPTIONS["video_codecs"]); self.ffmpeg_setting_widgets["c:v"].grid(row=1, column=1, padx=10, pady=5, sticky="ew")
        ctk.CTkLabel(video_frame, text="编码预设:").grid(row=2, column=0, padx=10, pady=5, sticky="w"); self.ffmpeg_setting_widgets["preset"] = ctk.CTkOptionMenu(video_frame, values=FFMPEG_OPTIONS["presets"]); self.ffmpeg_setting_widgets["preset"].grid(row=2, column=1, padx=10, pady=5, sticky="ew")
        ctk.CTkLabel(video_frame, text="CRF (质量):").grid(row=3, column=0, padx=10, pady=5, sticky="w"); crf_frame = ctk.CTkFrame(video_frame, fg_color="transparent"); crf_frame.grid(row=3, column=1, padx=10, pady=5, sticky="ew"); crf_frame.grid_columnconfigure(0, weight=1)
        self.ffmpeg_setting_widgets["crf"] = ctk.CTkSlider(crf_frame, from_=0, to=51, number_of_steps=51, command=lambda v: self.crf_var.set(str(int(v)))); self.ffmpeg_setting_widgets["crf"].grid(row=0, column=0, sticky="ew"); ctk.CTkLabel(crf_frame, textvariable=self.crf_var, width=30).grid(row=0, column=1, padx=5)
        ctk.CTkLabel(video_frame, text="视频比特率:").grid(row=4, column=0, padx=10, pady=5, sticky="w"); self.ffmpeg_setting_widgets["b:v"] = ctk.CTkEntry(video_frame); self.ffmpeg_setting_widgets["b:v"].grid(row=4, column=1, padx=10, pady=5, sticky="ew")
        audio_frame = ctk.CTkFrame(settings_tab, border_width=1); audio_frame.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")
        audio_frame.grid_columnconfigure(1, weight=1); ctk.CTkLabel(audio_frame, text="音频设置", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, columnspan=2, pady=5)
        ctk.CTkLabel(audio_frame, text="音频编码器:").grid(row=1, column=0, padx=10, pady=5, sticky="w"); self.ffmpeg_setting_widgets["c:a"] = ctk.CTkOptionMenu(audio_frame, values=FFMPEG_OPTIONS["audio_codecs"]); self.ffmpeg_setting_widgets["c:a"].grid(row=1, column=1, padx=10, pady=5, sticky="ew")
        ctk.CTkLabel(audio_frame, text="音频比特率:").grid(row=2, column=0, padx=10, pady=5, sticky="w"); self.ffmpeg_setting_widgets["b:a"] = ctk.CTkEntry(audio_frame); self.ffmpeg_setting_widgets["b:a"].grid(row=2, column=1, padx=10, pady=5, sticky="ew")
        output_frame = ctk.CTkFrame(settings_tab, border_width=1); output_frame.grid(row=1, column=0, columnspan=2, padx=10, pady=10, sticky="nsew")
        output_frame.grid_columnconfigure(1, weight=1); ctk.CTkLabel(output_frame, text="输出设置", font=ctk.CTkFont(size=14, weight="bold")).grid(row=0, columnspan=2, pady=5)
        ctk.CTkLabel(output_frame, text="输出格式:").grid(row=1, column=0, padx=10, pady=5, sticky="w"); self.ffmpeg_setting_widgets["f"] = ctk.CTkOptionMenu(output_frame, values=FFMPEG_OPTIONS["formats"]); self.ffmpeg_setting_widgets["f"].grid(row=1, column=1, padx=10, pady=5, sticky="ew")
        save_button = ctk.CTkButton(settings_tab, text="💾 保存当前主播的参数设置", command=self.save_streamer_ffmpeg_params); save_button.grid(row=2, column=0, columnspan=2, pady=20, sticky="ew", padx=10)
        self.disable_ffmpeg_settings()
    
    def redraw_streamer_list(self):
        for widget in self.streamer_scroll_frame.winfo_children(): widget.destroy()
        self.streamer_frames.clear()
        for room_id, data in sorted(self.streamers.items()):
            frame = ctk.CTkFrame(self.streamer_scroll_frame); frame.pack(fill="x", pady=5, padx=5); self.streamer_frames[room_id] = frame
            frame.grid_columnconfigure(1, weight=1)
            start_button = ctk.CTkButton(frame, text="▶️", command=lambda r=room_id: self.start_recording(r), width=40, fg_color="green"); start_button.grid(row=0, column=0, padx=(5,2), pady=5); frame.start_button = start_button
            info_frame = ctk.CTkFrame(frame, fg_color="transparent"); info_frame.grid(row=0, column=1, padx=2, pady=5, sticky="ew"); info_frame.grid_columnconfigure(1, weight=1)
            id_label = ctk.CTkLabel(info_frame, text=f"ID: {room_id}"); id_label.grid(row=0, column=0, sticky="w")
            remark_entry = ctk.CTkEntry(info_frame); remark_entry.grid(row=0, column=1, padx=10, sticky="ew"); remark_entry.insert(0, data.get("remark", "N/A")); frame.remark_entry = remark_entry
            save_remark_button = ctk.CTkButton(info_frame, text="💾", width=30, command=lambda r=room_id, e=remark_entry: self.save_remark(r, e.get())); save_remark_button.grid(row=0, column=2)
            status_label = ctk.CTkLabel(frame, text="空闲", width=60, text_color="gray"); status_label.grid(row=0, column=2, padx=2, pady=5); frame.status_label = status_label
            stop_button = ctk.CTkButton(frame, text="⏹️", command=lambda r=room_id: self.stop_recording(r), width=40, fg_color="red"); stop_button.grid(row=0, column=3, padx=2, pady=5); frame.stop_button = stop_button
            del_button = ctk.CTkButton(frame, text="🗑️", command=lambda r=room_id: self.remove_streamer(r), width=30, fg_color="gray"); del_button.grid(row=0, column=4, padx=(2,5), pady=5)
            for widget in [frame, info_frame, id_label]: widget.bind("<Button-1>", lambda e, r=room_id: self.on_streamer_selected(r))

    def add_streamer(self):
        dialog = AddStreamerDialog(self); self.wait_window(dialog)
        if result := dialog.result:
            room_id, remark = result["id"], result["remark"]
            if room_id in self.streamers: return messagebox.showwarning("警告", f"主播 {room_id} 已存在！")
            self.streamers[room_id] = {"remark": remark, "ffmpeg_params": {}}; save_json(STREAMERS_FILE, self.streamers)
            (RECORDING_PATH_BASE / room_id).mkdir(exist_ok=True); self.redraw_streamer_list()
            messagebox.showinfo("成功", f"主播 {remark} ({room_id}) 添加成功！")
            
    def remove_streamer(self, room_id):
        remark = self.streamers[room_id].get("remark", room_id)
        if messagebox.askyesno("确认删除", f"确定要删除主播 {remark} ({room_id}) 吗？"):
            if room_id in self.recording_threads and self.recording_threads[room_id].is_alive(): self.stop_recording(room_id); time.sleep(1)
            del self.streamers[room_id]; save_json(STREAMERS_FILE, self.streamers); self.redraw_streamer_list()
            if self.selected_room_id == room_id: self.selected_room_id = None; self.disable_ffmpeg_settings(); self.update_history_treeview(None)

    def save_remark(self, room_id, new_remark):
        if not new_remark.strip(): return messagebox.showwarning("提示", "备注不能为空。")
        self.streamers[room_id]["remark"] = new_remark; save_json(STREAMERS_FILE, self.streamers); messagebox.showinfo("成功", "备注已保存。", parent=self)

    def start_recording(self, room_id):
        if room_id in self.recording_threads and self.recording_threads[room_id].is_alive(): return
        thread = RecordingThread(self, room_id, self.get_ffmpeg_params_for_streamer(room_id)); thread.start(); self.recording_threads[room_id] = thread
    
    def stop_recording(self, room_id):
        if room_id in self.recording_threads and self.recording_threads[room_id].is_alive(): self.recording_threads[room_id].stop()

    def on_streamer_selected(self, room_id):
        if self.selected_room_id and self.selected_room_id in self.streamer_frames: self.streamer_frames[self.selected_room_id].configure(border_width=0)
        self.selected_room_id = room_id
        if room_id in self.streamer_frames: self.streamer_frames[room_id].configure(border_color="dodgerblue", border_width=2)
        self.update_history_treeview(room_id); self.load_ffmpeg_params_to_ui(room_id); self.enable_ffmpeg_settings()

    def update_ui_states_periodically(self):
        for room_id, frame in self.streamer_frames.items():
            thread = self.recording_threads.get(room_id)
            is_alive = thread and thread.is_alive()
            frame.start_button.configure(state="disabled" if is_alive else "normal")
            frame.stop_button.configure(state="normal" if is_alive else "disabled")
            if thread: frame.status_label.configure(text=thread.status, text_color=thread.status_color)
        self.after(1000, self.update_ui_states_periodically)

    def get_ffmpeg_params_for_streamer(self, room_id):
        final_params = {**DEFAULT_FFMPEG_PARAMS, **self.streamers.get(room_id, {}).get("ffmpeg_params", {})}
        if final_params.get("c:a") == "copy": final_params["bsf:a"] = "aac_adtstoasc"
        return final_params

    def toggle_patrol(self):
        if self.patrol_thread and self.patrol_thread.is_alive(): self.patrol_active.clear(); self.patrol_thread.join(); self.patrol_button.configure(text="▶️ 开启巡逻", fg_color="green")
        else: self.settings["patrol_start"] = self.patrol_start_entry.get(); self.settings["patrol_end"] = self.patrol_end_entry.get(); save_json(SETTINGS_FILE, self.settings); self.patrol_active.set(); self.patrol_thread = threading.Thread(target=self.patrol_loop, daemon=True); self.patrol_thread.start(); self.patrol_button.configure(text="⏹️ 停止巡逻", fg_color="red")
    
    def patrol_loop(self):
        while self.patrol_active.is_set():
            try: start_str, end_str = self.settings["patrol_start"], self.settings["patrol_end"]; start_time = datetime.datetime.strptime(start_str, "%H:%M").time(); end_time = datetime.datetime.strptime(end_str, "%H:%M").time(); now_time = datetime.datetime.now().time()
            except (ValueError, KeyError): self.patrol_status_var.set("巡逻失败: 时间格式错误"); self.patrol_active.wait(10); continue
            is_in_time = (start_time <= now_time <= end_time) if start_time <= end_time else (now_time >= start_time or now_time <= end_time)
            if is_in_time:
                self.patrol_status_var.set(f"巡逻中 ({start_str}-{end_str})")
                for room_id in list(self.streamers.keys()):
                    if not self.patrol_active.is_set(): break
                    if not (self.recording_threads.get(room_id) and self.recording_threads[room_id].is_alive()):
                        remark = self.streamers.get(room_id, {}).get('remark', room_id)
                        print(f"[Patrol] 正在检查主播 {remark}...")
                        self.start_recording(room_id); delay = random.uniform(5, 10)
                        print(f"[Patrol] 等待 {delay:.1f} 秒后继续...")
                        self.patrol_active.wait(delay)
            else: self.patrol_status_var.set("巡逻暂停 (非设定时间)"); self.patrol_active.wait(60) 
            self.patrol_active.wait(1)
        self.patrol_status_var.set("巡逻已停止")
        
    def on_closing(self):
        self.settings["patrol_start"] = self.patrol_start_entry.get(); self.settings["patrol_end"] = self.patrol_end_entry.get(); save_json(SETTINGS_FILE, self.settings)
        if self.patrol_active.is_set(): self.patrol_active.clear(); [t.join(timeout=1) for t in [self.patrol_thread] if t]
        for thread in self.recording_threads.values():
            if thread.is_alive(): thread.stop()
        self.destroy()

    def disable_ffmpeg_settings(self): [w.configure(state="disabled") for w in self.ffmpeg_setting_widgets.values()]
    def enable_ffmpeg_settings(self): [w.configure(state="normal") for w in self.ffmpeg_setting_widgets.values()]
    
    def load_ffmpeg_params_to_ui(self, room_id):
        params = self.streamers.get(room_id, {}).get("ffmpeg_params", {})
        for key, widget in self.ffmpeg_setting_widgets.items():
            value = params.get(key, "")
            if isinstance(widget, ctk.CTkOptionMenu): widget.set(value if value in widget.cget("values") else widget.cget("values")[0])
            elif isinstance(widget, ctk.CTkEntry): widget.delete(0, tk.END); widget.insert(0, str(value))
            elif isinstance(widget, ctk.CTkSlider): widget.set(float(value) if value and value.replace('.', '', 1).isdigit() else 23); self.crf_var.set(str(int(widget.get())))
    
    def save_streamer_ffmpeg_params(self):
        if not self.selected_room_id: return messagebox.showwarning("提示", "请先在左侧列表中点击选择一个主播。")
        params = {}
        for key, widget in self.ffmpeg_setting_widgets.items():
            value = (widget.get() if isinstance(widget, (ctk.CTkOptionMenu, ctk.CTkEntry)) else str(int(widget.get())))
            if value: params[key] = value
        self.streamers[self.selected_room_id]["ffmpeg_params"] = params; save_json(STREAMERS_FILE, self.streamers); messagebox.showinfo("成功", f"主播 {self.selected_room_id} 的参数已保存。")
    
    def update_history_treeview(self, room_id):
        for item in self.history_tree.get_children(): self.history_tree.delete(item)
        if not room_id: return
        folder = RECORDING_PATH_BASE / room_id
        if not folder.exists(): return
        files = [f for f in folder.glob("*.*") if f.suffix in ['.mkv', '.mp4', '.flv', '.ts'] and '_to_' in f.stem]
        for file in sorted(files, key=os.path.getmtime, reverse=True):
            try: parts = file.stem.split('_to_'); size_mb = f"{file.stat().st_size / (1024*1024):.2f} MB"; self.history_tree.insert("", tk.END, values=(file.name, parts[0].split('_')[-1], parts[1], size_mb))
            except IndexError: continue
    
    def play_history_video(self):
        if not self.selected_room_id: return messagebox.showwarning("提示", "请先选择主播。")
        if not (selected_item := self.history_tree.focus()): return messagebox.showwarning("提示", "请在历史记录中选择一个视频文件。")
        filename = self.history_tree.item(selected_item, 'values')[0]; filepath = RECORDING_PATH_BASE / self.selected_room_id / filename
        if filepath.exists(): os.startfile(filepath)
        else: messagebox.showerror("错误", "文件不存在！")

    def open_history_folder(self):
        if not self.selected_room_id: return messagebox.showwarning("提示", "请先选择主播。")
        folder_path = RECORDING_PATH_BASE / self.selected_room_id; folder_path.mkdir(exist_ok=True); os.startfile(folder_path)

    def delete_history_video(self):
        if not self.selected_room_id: return messagebox.showwarning("提示", "请先选择主播。")
        if not (selected_item := self.history_tree.focus()): return messagebox.showwarning("提示", "请在历史记录中选择一个视频文件。")
        filename = self.history_tree.item(selected_item, 'values')[0]; filepath = RECORDING_PATH_BASE / self.selected_room_id / filename
        if messagebox.askyesno("确认删除", f"确定要永久删除文件 {filename} 吗？"):
            try: os.remove(filepath); messagebox.showinfo("成功", "文件已删除。"); self.update_history_treeview(self.selected_room_id)
            except Exception as e: messagebox.showerror("错误", f"删除文件失败: {e}")

# --- 录制线程类 (V7) ---
class RecordingThread(threading.Thread):
    def __init__(self, app_instance, room_id, ffmpeg_params):
        super().__init__(daemon=True); self.app, self.room_id, self.ffmpeg_params = app_instance, room_id, ffmpeg_params
        self.live_url, self.process, self._stop_event = f"https://live.douyin.com/{self.room_id}", None, threading.Event()
        self.status, self.status_color = "检查中...", "orange"
    def run(self):
        print(f"[{self.room_id}] 线程启动，开始检查..."); session = streamlink.Streamlink()
        session.set_option("http-headers", {"User-Agent": CHROME_USER_AGENT, "Referer": self.live_url})
        try: streams = session.streams(self.live_url)
        except Exception as e: print(f"[{self.room_id}] Streamlink在获取流时发生异常: {e}"); streams = None
        if not streams: print(f"[{self.room_id}] 未开播或无法获取直播流 (可能被403拦截)。"); self.status, self.status_color = "未开播", "yellow"; return
        stream_url = streams["best"].url
        print(f"[{self.room_id}] 已获取到直播流地址，准备开始录制。"); self.status, self.status_color = "录制中", "green"
        start_time_str = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir = RECORDING_PATH_BASE / self.room_id; file_format = self.ffmpeg_params.get("f", "mkv")
        temp_filepath = output_dir / f"{self.room_id}_{start_time_str}_recording.{file_format}.tmp"
        command = ['ffmpeg', '-i', stream_url]; [command.extend([f'-{k}', str(v)]) for k, v in self.ffmpeg_params.items()]; command.append(str(temp_filepath))
        print(f"[{self.room_id}] FFmpeg 命令: {' '.join(command)}")
        try:
            startupinfo = subprocess.STARTUPINFO() if os.name == 'nt' else None
            if os.name == 'nt': startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            self.process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, startupinfo=startupinfo); self.process.wait()
        except FileNotFoundError: print(f"[{self.room_id}] FFmpeg执行失败！请确保已正确安装并添加到系统环境变量中。"); self.status, self.status_color = "FFmpeg错误", "red"; return
        except Exception as e: print(f"[{self.room_id}] FFmpeg 录制出错: {e}"); self.status, self.status_color = "录制出错", "red"; return
        status_text = "手动停止" if self._stop_event.is_set() else "自动结束"
        print(f"[{self.room_id}] 录制{status_text}。"); self.status, self.status_color = status_text, "gray"
        final_filepath = output_dir / f"{self.room_id}_{start_time_str}_to_{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.{file_format}"
        if temp_filepath.exists():
            try: os.rename(temp_filepath, final_filepath); print(f"[{self.room_id}] 文件已保存为: {final_filepath.name}")
            except Exception as e: print(f"[{self.room_id}] 重命名文件失败: {e}")
        elif not self._stop_event.is_set(): print(f"[{self.room_id}] 临时文件未找到。")
        if self.app.selected_room_id == self.room_id: self.app.after(100, lambda: self.app.update_history_treeview(self.room_id))
    def stop(self):
        if self.process and self.process.poll() is None:
            self._stop_event.set(); print(f"[{self.room_id}] 正在发送停止信号给 FFmpeg...")
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except subprocess.TimeoutExpired: print(f"[{self.room_id}] FFmpeg 未在5秒内响应，强制终止。"); self.process.kill()

# --- 程序入口 ---
if __name__ == "__main__":
    ctk.set_appearance_mode("System"); ctk.set_default_color_theme("blue"); app = DouyinRecorderApp(); app.mainloop()
