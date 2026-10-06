#ifndef PayloadZip
  #error PayloadZip is required
#endif
#ifndef Output
  #error Output is required
#endif
[Setup]
AppId={{41B13E65-4491-4A32-9754-772C236346B4}
AppName=轻仓 WMS V1.3 保留数据升级
AppVersion=1.3.0
AppPublisher=宋子豪
DefaultDirName={localappdata}\LightWarehouseWMS
UsePreviousAppDir=no
DisableDirPage=no
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Output}
OutputBaseFilename=轻仓WMS升级包-V1.3-Windows-x64
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
Uninstallable=no
CloseApplications=no
RestartApplications=no
AppMutex=Local\LightWarehouseWMSStandalone
InfoBeforeFile=升级说明.txt
SetupLogging=yes
[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
[Files]
Source: "{#PayloadZip}"; DestName: "wms-upgrade.zip"; Flags: dontcopy
Source: "upgrade.py"; Flags: dontcopy
[Run]
Filename: "{app}\轻仓WMS.exe"; Description: "启动 V1.3，使用原账号登录"; Flags: nowait postinstall skipifsilent
[Code]
var UpgradeCompleted: Boolean;
function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code: Integer;
begin
  Result := '';
  if UpgradeCompleted then Exit;
  if not FileExists(ExpandConstant('{app}\data\config.json')) or
     not FileExists(ExpandConstant('{app}\python\python.exe')) then begin
    Result := '请选择此前轻仓 WMS 独立版的原安装目录。本包不会新建或清空数据库。';
    Exit;
  end;
  ExtractTemporaryFile('wms-upgrade.zip');
  ExtractTemporaryFile('upgrade.py');
  WizardForm.PreparingLabel.Caption := '正在验证旧版、备份并升级。请保持电脑开机，等待完成。';
  if not Exec(ExpandConstant('{app}\python\python.exe'),
      '-I "' + ExpandConstant('{tmp}\upgrade.py') + '" --root "' + ExpandConstant('{app}') +
      '" --payload "' + ExpandConstant('{tmp}\wms-upgrade.zip') + '"',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then begin
    Result := '无法启动升级程序，请保留原安装目录并检查文件权限。';
    Exit;
  end;
  case Code of
    0: UpgradeCompleted := True;
    1: Result := '旧版程序、配置或升级包校验未通过。仅支持此前官方V1.2独立安装版，不能升级开发版或正式服务器目录。原数据没有清空。';
    2: Result := '系统仍在运行或端口被占用。请在控制窗口点击“停止并退出”，退出其他使用者后重试。不会强停其他程序。';
    3: Result := '数据库版本或升级前对账检查未通过。请保留原数据及data\upgrade-backups中的报告，联系维护人员。';
    4: Result := '数据库启动、备份或停止未完成。原数据保留，请检查磁盘空间及data\upgrade-backups中的报告。';
    6: Result := '发现上次未完成的升级。系统已暂停启动以保护数据，请保留data及升级备份并联系维护人员。';
  else
    Result := '升级未完成。原数据与已生成的备份保留，请不要重装或恢复旧SQL；将data\upgrade-backups中的report.json交给维护人员核查。';
  end;
end;
