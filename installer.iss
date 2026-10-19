[Setup]
AppName=MyDM Lite
AppVersion=4.1
DefaultDirName={autopf}\MyDM
DefaultGroupName=MyDM
OutputBaseFilename=MyDM_Lite_Setup_v4.1
Compression=lzma2
SolidCompression=yes
CloseApplications=yes

[Files]
Source: "dist\MyDM.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\MyDM"; Filename: "{app}\MyDM.exe"
Name: "{autodesktop}\MyDM"; Filename: "{app}\MyDM.exe"

[Run]
Filename: "{app}\MyDM.exe"; Description: "Launch MyDM"; Flags: nowait postinstall skipifsilent
