#define MyAppName "公众号改写助手"
#define MyAppPublisher "蓝血研究"
#define MyAppExeName "公众号改写助手.exe"
#define MyAppVersion "1.3.0"
#define MyRemoteUrl "http://47.99.126.8/"
#define BuildDir "..\dist\公众号改写助手"
#ifndef MyAppId
  #define MyAppId "{{B5B0F085-6C6D-44F5-9D53-3895929B36EE}"
#endif
#ifndef MyOutputBaseFilename
  #define MyOutputBaseFilename MyAppName + "-生产环境安装包-" + MyAppVersion + "-20260730"
#endif

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist\installers
OutputBaseFilename={#MyOutputBaseFilename}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no
SetupLogging=yes

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: unchecked

[Dirs]
Name: "{app}\data"; Flags: uninsneveruninstall
Name: "{app}\data\logs"; Flags: uninsneveruninstall
Name: "{app}\data\templates"; Flags: uninsneveruninstall
Name: "{app}\data\generated_images"; Flags: uninsneveruninstall
Name: "{app}\data\model_tests"; Flags: uninsneveruninstall

[InstallDelete]
; 原地升级前仅清理旧程序依赖，避免已删除的模块与新版本混装。
; 用户数据、config.yaml 和登录凭据均不在此目录，不会被删除。
Type: filesandordirs; Name: "{app}\_internal"

[Files]
Source: "{#BuildDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#BuildDir}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\config.example.yaml"; DestDir: "{app}"; DestName: "config.example.yaml"; Flags: ignoreversion
Source: "..\config.example.yaml"; DestDir: "{app}"; DestName: "config.yaml"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "..\.env.example"; DestDir: "{app}"; DestName: ".env.example"; Flags: ignoreversion
Source: "..\data\hot_topics.json"; DestDir: "{app}\data"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "..\data\keywords.txt"; DestDir: "{app}\data"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "..\data\peer_topics.json"; DestDir: "{app}\data"; Flags: onlyifdoesntexist uninsneveruninstall
Source: "运营使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "版本说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--remote-url {#MyRemoteUrl}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Parameters: "--remote-url {#MyRemoteUrl}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--remote-url {#MyRemoteUrl}"; Description: "启动{#MyAppName}"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent

[Code]
procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    WizardForm.StatusLabel.Caption := '正在验证安装包运行环境…';
    if (not Exec(
      ExpandConstant('{app}\{#MyAppExeName}'),
      '--self-test',
      ExpandConstant('{app}'),
      SW_HIDE,
      ewWaitUntilTerminated,
      ResultCode
    )) or (ResultCode <> 0) then
    begin
      RaiseException(
        '安装后自检失败。请查看 ' +
        ExpandConstant('{app}\data\logs\package-self-test.json')
      );
    end;
  end;
end;
