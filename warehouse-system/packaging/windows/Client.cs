using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("轻仓 WMS 独立版")]
[assembly: AssemblyProduct("轻仓 WMS 独立版")]
[assembly: AssemblyVersion("1.3.0.0")]

internal class Client : Form
{
    readonly string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\');
    readonly JavaScriptSerializer json = new JavaScriptSerializer();
    readonly Label status = new Label { Left=24, Top=22, Width=540, Height=52 };
    readonly TextBox username = new TextBox { Left=150, Top=88, Width=370, Text="admin", MaxLength=64 };
    readonly TextBox password = new TextBox { Left=150, Top=126, Width=370, UseSystemPasswordChar=true, MaxLength=128 };
    readonly TextBox confirm = new TextBox { Left=150, Top=164, Width=370, UseSystemPasswordChar=true, MaxLength=128 };
    readonly Button start = new Button { Left=24, Top=222, Width=130, Height=36, Text="启动系统" };
    readonly Button open = new Button { Left=166, Top=222, Width=130, Height=36, Text="打开系统", Enabled=false };
    readonly Button backup = new Button { Left=308, Top=222, Width=110, Height=36, Text="备份数据", Enabled=false };
    readonly Button stop = new Button { Left=430, Top=222, Width=120, Height=36, Text="停止并退出" };
    readonly NotifyIcon tray = new NotifyIcon { Icon=SystemIcons.Application, Text="轻仓 WMS 独立版" };
    Process controller;
    string url;
    bool working, exiting, startupError;

    [STAThread]
    static void Main()
    {
        using (Mutex mutex = new Mutex(false, "Local\\LightWarehouseWMSStandalone"))
        {
            if (!mutex.WaitOne(0)) { MessageBox.Show("轻仓 WMS 已在运行，请从任务栏或系统托盘打开。", "轻仓 WMS"); return; }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new Client());
        }
    }

    Client()
    {
        Text="轻仓 WMS · 独立运行"; ClientSize=new Size(576,290);
        Icon=Icon.ExtractAssociatedIcon(Application.ExecutablePath); tray.Icon=Icon;
        StartPosition=FormStartPosition.CenterScreen; FormBorderStyle=FormBorderStyle.FixedDialog;
        MaximizeBox=false; Font=new Font("Microsoft YaHei UI",9);
        Controls.AddRange(new Control[] {status, username,password,confirm,start,open,backup,stop,
            new Label {Left=24,Top=92,Width=120,Text="管理员用户名"},
            new Label {Left=24,Top=130,Width=120,Text="管理员密码"},
            new Label {Left=24,Top=168,Width=120,Text="再次输入密码"}});
        status.Text="首次启动请设置管理员密码（14～128 位，不能包含用户名）。\n此独立版使用本机的新数据库。";
        start.Click += (s,e) => StartSystem(); open.Click += (s,e) => OpenBrowser();
        backup.Click += (s,e) => { backup.Enabled=false; status.Text="正在备份，请稍候…"; controller.StandardInput.WriteLine("backup"); };
        stop.Click += async (s,e) => await StopSystem();
        tray.DoubleClick += (s,e) => { Show(); WindowState=FormWindowState.Normal; Activate(); };
        ContextMenuStrip menu=new ContextMenuStrip();
        menu.Items.Add("打开控制窗口",null,(s,e)=>{ Show(); Activate(); });
        menu.Items.Add("打开系统",null,(s,e)=>OpenBrowser());
        menu.Items.Add("停止并退出",null,async (s,e)=>await StopSystem()); tray.ContextMenuStrip=menu;
        FormClosing += (s,e) => {
            if (exiting) return;
            if (controller != null && !controller.HasExited) {
                e.Cancel=true;
                if (working) { MessageBox.Show("正在启动，请等待完成后再停止系统。",Text); return; }
                tray.Visible=true; Hide();
            }
        };
        Shown += (s,e) => {
            string config=Path.Combine(root,"data","config.json");
            if (File.Exists(config)) {
                try {
                    var values=json.Deserialize<Dictionary<string,object>>(File.ReadAllText(config));
                    if (values.ContainsKey("ready") && (bool)values["ready"]) {
                        username.Enabled=password.Enabled=confirm.Enabled=false;
                        StartSystem();
                    }
                } catch { status.Text="配置文件无法读取；请保留 data 目录并联系维护人员。"; start.Enabled=false; }
            }
        };
    }

    void StartSystem()
    {
        if (working || (controller != null && !controller.HasExited)) return;
        if (password.Enabled && (password.Text != confirm.Text || password.Text.Length<14 ||
            username.Text.Length<3 || password.Text.ToLowerInvariant().Contains(username.Text.ToLowerInvariant()))) {
            MessageBox.Show("请检查用户名、密码长度和两次输入。密码至少 14 位，且不能包含用户名。",Text); return;
        }
        try {
            working=true; startupError=false; start.Enabled=false; stop.Enabled=false;
            status.Text="正在启动，请稍候…";
            ProcessStartInfo info=new ProcessStartInfo(Path.Combine(root,"python","python.exe"),
                "-I \""+Path.Combine(root,"controller","runtime.py")+"\" run \""+Path.Combine(root,"data")+"\"");
            info.WorkingDirectory=root; info.UseShellExecute=false; info.CreateNoWindow=true;
            info.RedirectStandardInput=true; info.RedirectStandardOutput=true; info.RedirectStandardError=true;
            info.StandardOutputEncoding=System.Text.Encoding.UTF8;
            controller=new Process { StartInfo=info, EnableRaisingEvents=true };
            controller.OutputDataReceived += (s,e) => { if(e.Data != null) HandleEvent(e.Data); };
            // Runtime error output is intentionally not shown verbatim; logs stay in the private data folder.
            controller.ErrorDataReceived += (s,e) => {};
            controller.Exited += (s,e) => SafeUI(()=>{
                working=false; start.Enabled=true; stop.Enabled=true; open.Enabled=backup.Enabled=false;
                if(!exiting && !startupError) status.Text="系统已停止。数据保留在安装目录的 data 文件夹。";
            });
            controller.Start(); controller.BeginOutputReadLine(); controller.BeginErrorReadLine();
            controller.StandardInput.WriteLine(AsciiJson(json.Serialize(new {username=username.Text.Trim(),password=password.Text})));
            controller.StandardInput.Flush(); password.Clear(); confirm.Clear();
        } catch {
            working=false; start.Enabled=stop.Enabled=true;
            MessageBox.Show("无法启动自带运行环境，请检查安装是否完整。",Text);
        }
    }

    void SafeUI(Action action)
    {
        if(!IsDisposed && IsHandleCreated) try { BeginInvoke(action); } catch(InvalidOperationException) {}
    }

    // Keep the pipe ASCII-compatible even on Windows using legacy input code pages.
    static string AsciiJson(string value)
    {
        var result=new System.Text.StringBuilder();
        foreach(char character in value) {
            if(character > 127) result.Append("\\u").Append(((int)character).ToString("x4"));
            else result.Append(character);
        }
        return result.ToString();
    }

    void HandleEvent(string line)
    {
        Dictionary<string,object> values;
        try { values=json.Deserialize<Dictionary<string,object>>(line); } catch { return; }
        if (!values.ContainsKey("event")) return;
        SafeUI(()=>{
            string kind=Convert.ToString(values["event"]);
            if(kind=="progress") status.Text=Convert.ToString(values["message"]);
            if(kind=="ready") {
                url=Convert.ToString(values["url"]); working=false; open.Enabled=backup.Enabled=stop.Enabled=true;
                username.Enabled=password.Enabled=confirm.Enabled=false;
                status.Text="系统已运行。关闭此窗口可保留在系统托盘。\n退出请点击“停止并退出”；数据会保留。";
                tray.Visible=true; OpenBrowser();
            }
            if(kind=="backup") {
                backup.Enabled=true; status.Text="备份已完成。请将备份另存到其他磁盘。";
                MessageBox.Show("备份文件：\n"+Convert.ToString(values["path"])+"\n\n对应 SHA256 校验文件已同时生成。",Text);
            }
            if(kind=="error") {
                startupError=true;
                backup.Enabled=url != null;
                status.Text=Convert.ToString(values["message"])+"\n错误类型："+Convert.ToString(values["error"]);
                MessageBox.Show(status.Text,Text,MessageBoxButtons.OK,MessageBoxIcon.Warning);
            }
        });
    }

    void OpenBrowser()
    {
        if(url==null) return;
        try { Process.Start(new ProcessStartInfo(url) {UseShellExecute=true}); }
        catch { MessageBox.Show("请在浏览器打开："+url,Text); }
    }

    async Task StopSystem()
    {
        if(working) { MessageBox.Show("正在启动，请稍后停止。",Text); return; }
        stop.Enabled=false;
        if(controller != null && !controller.HasExited) {
            status.Text="正在停止系统并关闭数据库，请稍候…";
            try { controller.StandardInput.WriteLine("stop"); controller.StandardInput.Flush(); } catch(IOException) {}
            bool ended=await Task.Run(()=>controller.WaitForExit(60000));
            if(!ended) { stop.Enabled=true; status.Text="数据库尚未停止，请保留此窗口并稍后重试。"; return; }
            if(controller.ExitCode != 0) {
                stop.Enabled=true; status.Text="停止过程出现问题，请检查 data 日志；不强行关闭数据库。"; return;
            }
        }
        exiting=true; tray.Visible=false; tray.Dispose(); Close();
    }
}
