#define MyAppName "PalPlus"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.1"
#endif
#ifndef MyAppURL
  #define MyAppURL "https://github.com/shin86dev/palworld-companion"
#endif
#define MyAppPublisher "PalPlus contributors"
#define MyAppExeName "PalPlus.exe"

[Setup]
AppId={{B2C6B52F-8F11-4B60-B2AF-1D7834369E86}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}/issues
AppUpdatesURL={#MyAppURL}/releases/latest
DefaultDirName={localappdata}\Programs\PalPlus
DefaultGroupName=PalPlus
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
SetupIconFile=..\assets\palplus.ico
OutputDir=..\dist\installer
OutputBaseFilename=PalPlus-Setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#MyAppExeName}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=PalPlus installer
VersionInfoProductName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "..\dist\PalPlus\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\PalPlus"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\PalPlus"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch PalPlus"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent
