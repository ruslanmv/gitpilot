; Inno Setup Script for GitPilot
; This installs to Program Files and shows in Add/Remove Programs automatically.

#define AppName "GitPilot"
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#define AppPublisher "GitPilot"
#define AppURL "https://github.com/ruslanmv/gitpilot"
#define AppExeName "gitpilot.exe"
#define OutputName "gitpilot-setup"

[Setup]
AppId={{8F4A7E2C-3D5B-4A1F-9E6D-2C8B7A3F5E1D}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppUpdatesURL={#AppURL}
DefaultDirName={autopf}\GitPilot
DefaultGroupName=GitPilot
AllowNoIcons=yes
OutputDir=.
OutputBaseFilename={#OutputName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\gitpilot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\GitPilot"; Filename: "{app}\{#AppExeName}"
Name: "{group}\Open GitPilot in Browser"; Filename: "http://127.0.0.1:8000"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\GitPilot"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
