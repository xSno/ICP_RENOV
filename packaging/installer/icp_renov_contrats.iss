; ICP Renov — Contrats 1.0.0
; Stable installer identity. User data is deliberately outside {app}.

#define AppName "ICP Renov — Contrats"
#define AppPublisher "ICP Renov"
#define AppVersion "1.0.0"
#define AppExeName "ICP Renov - Contrats.exe"

[Setup]
AppId={{D0A5EB7C-5F81-46F1-91E5-D3780ADFB071}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\ICP Renov\Contrats
DefaultGroupName=ICP Renov
DisableProgramGroupPage=yes
OutputDir=..\..\installer-output
OutputBaseFilename=ICP-Renov-Contrats-Setup-1.0.0
UninstallDisplayIcon={app}\{#AppExeName}
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
SignTool=icp-renov
SignedUninstaller=yes

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Files]
Source: "..\..\dist\ICP Renov - Contrats\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\ICP Renov"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\ICP Renov"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"

[Code]
function LibreOfficePresent(): Boolean;
begin
  Result :=
    FileExists(ExpandConstant('{autopf}\LibreOffice\program\soffice.com')) or
    FileExists(ExpandConstant('{autopf}\LibreOffice\program\soffice.exe')) or
    FileExists(ExpandConstant('{pf32}\LibreOffice\program\soffice.com')) or
    FileExists(ExpandConstant('{pf32}\LibreOffice\program\soffice.exe'));
end;

function InitializeSetup(): Boolean;
begin
  Result := LibreOfficePresent();
  if not Result then
    MsgBox(
      'La génération des PDF nécessite LibreOffice. Veuillez demander à l''administrateur de l''installer, puis relancez l''installation d''ICP Renov.',
      mbInformation, MB_OK);
end;
