; Inno Setup Script para Tutor Socrático
; La versión llega desde tools/construir.py (/DMyAppVersion=...); sin ella no se compila
#ifndef MyAppVersion
  #error "Falta /DMyAppVersion=<versión>: construye con python -m tools.construir"
#endif

[Setup]
; AppId fijo para que nuevas versiones actualicen la instalación previa
AppId={{8B5C9A31-4F2E-4D78-9C21-6A81C7E83D01}}
AppName=Tutor Socratico
AppVersion={#MyAppVersion}
AppVerName=Tutor Socratico {#MyAppVersion}
AppPublisher=Tutor Socratico
DefaultDirName={localappdata}\Programs\Tutor Socratico
DefaultGroupName=Tutor Socratico
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir=..\salida
OutputBaseFilename=TutorSocratico-Instalador-{#MyAppVersion}
SetupIconFile=tutor.ico
UninstallDisplayIcon={app}\TutorSocratico.exe
Compression=lzma2/normal
SolidCompression=yes

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; El modelo GGUF no se comprime para reducir drásticamente el tiempo de empaquetado
Source: "..\dist\TutorSocratico\modelo\*"; DestDir: "{app}\modelo"; Flags: ignoreversion recursesubdirs createallsubdirs nocompression
Source: "..\dist\TutorSocratico\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "modelo\*"
Source: "licencias\*"; DestDir: "{app}\licencias"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Tutor Socrático"; Filename: "{app}\TutorSocratico.exe"
Name: "{group}\{cm:UninstallProgram,Tutor Socrático}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Tutor Socrático"; Filename: "{app}\TutorSocratico.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\TutorSocratico.exe"; Description: "{cm:LaunchProgram,Tutor Socrático}"; Flags: nowait postinstall skipifsilent
