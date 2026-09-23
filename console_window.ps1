param([string]$mode)
Add-Type -Name Win -Namespace Native -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool ShowWindow(System.IntPtr hWnd, int nCmdShow);
[DllImport("kernel32.dll")] public static extern System.IntPtr GetConsoleWindow();
'@
$h = [Native.Win]::GetConsoleWindow()
if ($h -eq [System.IntPtr]::Zero) { exit 1 }
if ($mode -eq 'hide') { [Native.Win]::ShowWindow($h, 0) | Out-Null } else { [Native.Win]::ShowWindow($h, 5) | Out-Null }
exit 0
