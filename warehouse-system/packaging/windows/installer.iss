#ifndef Payload
  #error Payload must point to a verified staged package
#endif
#ifndef Output
  #error Output directory is required
#endif

[Setup]
AppId={{924D0ED8-98CF-4BBF-B1F2-EE1E1D695B72}
AppName=轻仓 WMS 独立版
AppVersion=1.3.0
AppPublisher=宋子豪
DefaultDirName={localappdata}\LightWarehouseWMS
DefaultGroupName=轻仓 WMS 独立版
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Output}
OutputBaseFilename=轻仓WMS安装包-V1.3-Windows-x64
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\轻仓WMS.exe
CloseApplications=no
RestartApplications=no
AppMutex=Local\LightWarehouseWMSStandalone
InfoBeforeFile=安装说明.txt
SetupLogging=yes

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "data\*,VC_redist.x64.exe"
Source: "{#Payload}\VC_redist.x64.exe"; Flags: dontcopy

[Icons]
Name: "{userdesktop}\轻仓 WMS 独立版"; Filename: "{app}\轻仓WMS.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\轻仓 WMS 独立版"; Filename: "{app}\轻仓WMS.exe"; WorkingDir: "{app}"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{group}\卸载轻仓 WMS（保留数据）"; Filename: "{uninstallexe}"

[Run]
Filename: "{app}\轻仓WMS.exe"; Description: "启动轻仓 WMS 并设置管理员账号"; Flags: nowait postinstall skipifsilent

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"

[Code]
function VCRuntimeInstalled: Boolean;
var Installed, Major, Minor: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) and (Installed = 1)
    and RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Major', Major)
    and RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Minor', Minor)
    and ((Major > 14) or ((Major = 14) and (Minor >= 44)));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var ResultCode: Integer;
    Entry: TFindRec;
    HasFiles: Boolean;
begin
  Result := '';
  if FileExists(ExpandConstant('{app}\data\config.json')) or DirExists(ExpandConstant('{app}\data\mysql')) then begin
    Result := '此目录已有独立版数据库。当前安装包用于新安装，不会覆盖或自动升级已有数据；请选择新的空目录。';
    Exit;
  end;
  HasFiles := False;
  if FindFirst(ExpandConstant('{app}\*'), Entry) then begin
    try
      repeat
        if (Entry.Name <> '.') and (Entry.Name <> '..') then HasFiles := True;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
  if HasFiles then begin
    Result := '安装目录已有其他文件。请选择新的空目录，安装程序不会覆盖已有程序。';
    Exit;
  end;
  if not VCRuntimeInstalled then begin
    ExtractTemporaryFile('VC_redist.x64.exe');
    if not ShellExec('runas', ExpandConstant('{tmp}\VC_redist.x64.exe'), '/install /passive /norestart', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Result := '微软运行库安装未完成。请允许管理员权限安装运行库后重试。'
    else if (ResultCode <> 0) and (ResultCode <> 1638) and (ResultCode <> 3010) then
      Result := '微软运行库安装失败，请检查 Windows 后重试。'
    else if ResultCode = 3010 then NeedsRestart := True;
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := SuppressibleMsgBox('卸载仅移除程序，data 文件夹中的数据库、配置和备份将保留。请先在控制窗口点击“停止并退出”。是否继续？', mbConfirmation, MB_YESNO, IDYES) = IDYES;
end;
