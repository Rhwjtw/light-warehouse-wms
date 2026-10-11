#ifndef Payload
  #error Payload is required
#endif
#ifndef PayloadZip
  #error PayloadZip is required
#endif
#ifndef Output
  #error Output is required
#endif
[Setup]
AppId={{924D0ED8-98CF-4BBF-B1F2-EE1E1D695B72}
AppName=轻仓 WMS 独立版
AppVersion=1.3.2
AppPublisher=宋子豪
DefaultDirName={localappdata}\LightWarehouseWMS
DefaultGroupName=轻仓 WMS 独立版
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Output}
OutputBaseFilename=轻仓WMS安装包-V1.3.2-Windows-x64
Compression=lzma2/normal
SolidCompression=yes
WizardStyle=modern
DisableProgramGroupPage=yes
DisableDirPage=no
UsePreviousAppDir=yes
UninstallDisplayIcon={app}\轻仓WMS.exe
SetupIconFile=assets\wms.ico
CloseApplications=no
RestartApplications=no
ChangesAssociations=yes
AppMutex=Local\LightWarehouseWMSStandalone
InfoBeforeFile=安装说明.txt
SetupLogging=yes
[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"
[Files]
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "data\*,VC_redist.x64.exe"; Check: InstallFreshFiles
Source: "{#Payload}\VC_redist.x64.exe"; Flags: dontcopy
Source: "{#PayloadZip}"; DestName: "wms-upgrade.zip"; Flags: dontcopy
Source: "upgrade.py"; Flags: dontcopy
[Icons]
Name: "{userdesktop}\轻仓 WMS 独立版"; Filename: "{app}\轻仓WMS.exe"; WorkingDir: "{app}"; Tasks: desktopicon
Name: "{group}\轻仓 WMS 独立版"; Filename: "{app}\轻仓WMS.exe"; WorkingDir: "{app}"
Name: "{group}\使用说明"; Filename: "{app}\使用说明.txt"
Name: "{group}\卸载轻仓 WMS（保留数据）"; Filename: "{uninstallexe}"
[Run]
Filename: "{app}\轻仓WMS.exe"; Description: "启动轻仓 WMS"; Flags: nowait postinstall skipifsilent
[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"
[Code]
var UpgradeMode, UpgradeCompleted: Boolean;
function InstallFreshFiles: Boolean;
begin
  Result := not UpgradeMode;
end;

function VCRuntimeInstalled: Boolean;
var Installed, Major, Minor: Cardinal;
begin
  Result := RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) and (Installed = 1)
    and RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Major', Major)
    and RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Minor', Minor)
    and ((Major > 14) or ((Major = 14) and (Minor >= 44)));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var Code, I: Integer;
    Entry: TFindRec;
    HasFiles: Boolean;
    Lines: TArrayOfString;
begin
  Result := '';
  UpgradeMode := FileExists(ExpandConstant('{app}\data\config.json')) or DirExists(ExpandConstant('{app}\data\mysql'));
  if UpgradeMode and UpgradeCompleted then Exit;
  if not UpgradeMode then begin
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
      Result := '所选目录包含其他文件，请选择空目录。安装器不会覆盖其他程序或数据。';
      Exit;
    end;
  end;
  if not VCRuntimeInstalled then begin
    ExtractTemporaryFile('VC_redist.x64.exe');
    if not ShellExec('runas', ExpandConstant('{tmp}\VC_redist.x64.exe'), '/install /passive /norestart', '', SW_HIDE, ewWaitUntilTerminated, Code) then
      Result := '微软运行库安装未完成，请允许安装后重试。'
    else if (Code <> 0) and (Code <> 1638) and (Code <> 3010) then
      Result := '微软运行库安装未完成，请检查Windows后重试。'
    else if Code = 3010 then NeedsRestart := True;
    if Result <> '' then Exit;
  end;
  if not UpgradeMode then begin
    Log('WMS_INSTALL_MODE=fresh');
    Exit;
  end;
  Log('WMS_INSTALL_MODE=preserve_data_upgrade');
  if not FileExists(ExpandConstant('{app}\python\python.exe')) then begin
    Result := '找到原数据，但原运行程序不完整。数据已保留，请勿卸载或删除data。';
    Exit;
  end;
  ExtractTemporaryFile('wms-upgrade.zip');
  ExtractTemporaryFile('upgrade.py');
  WizardForm.PreparingLabel.Caption := '已找到轻仓 WMS，正在自动备份并保留数据升级，请稍候…';
  if not Exec(ExpandConstant('{app}\python\python.exe'),
      '-I "' + ExpandConstant('{tmp}\upgrade.py') + '" --root "' + ExpandConstant('{app}') +
      '" --payload "' + ExpandConstant('{tmp}\wms-upgrade.zip') +
      '" --result "' + ExpandConstant('{tmp}\upgrade-result.txt') + '"',
      ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, Code) then begin
    Result := '无法启动安装升级程序，请检查原安装目录的文件权限。原数据保留。';
    Exit;
  end;
  if Code = 0 then begin
    UpgradeCompleted := True;
    Exit;
  end;
  Result := '安装升级未完成，原数据与备份保留。';
  if LoadStringsFromFile(ExpandConstant('{tmp}\upgrade-result.txt'), Lines) then begin
    Result := '';
    for I := 0 to GetArrayLength(Lines)-1 do Result := Result + Lines[I] + #13#10;
  end;
end;

function InitializeUninstall: Boolean;
begin
  Result := SuppressibleMsgBox('卸载只移除程序，data中的数据库和备份会保留。请先停止轻仓WMS。是否继续？', mbConfirmation, MB_YESNO, IDYES) = IDYES;
end;
