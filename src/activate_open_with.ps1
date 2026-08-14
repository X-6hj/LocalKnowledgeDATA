$ErrorActionPreference = 'SilentlyContinue'

Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @"
using System;
using System.Runtime.InteropServices;

public static class OpenWithTaskbarClick {
    [StructLayout(LayoutKind.Sequential)]
    public struct Point { public int X; public int Y; }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr FindWindow(string className, string windowName);

    [DllImport("user32.dll")]
    public static extern bool GetCursorPos(out Point point);

    [DllImport("user32.dll")]
    public static extern bool SetCursorPos(int x, int y);

    [DllImport("user32.dll")]
    public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extraInfo);

    [DllImport("user32.dll")]
    public static extern bool SetProcessDPIAware();
}
"@

[void][OpenWithTaskbarClick]::SetProcessDPIAware()
$target = $null

for ($attempt = 0; $attempt -lt 100 -and $null -eq $target; $attempt++) {
    $handle = [OpenWithTaskbarClick]::FindWindow('Shell_TrayWnd', $null)
    $taskbar = [Windows.Automation.AutomationElement]::FromHandle($handle)
    $elements = $taskbar.FindAll(
        [Windows.Automation.TreeScope]::Descendants,
        [Windows.Automation.Condition]::TrueCondition
    )

    for ($index = 0; $index -lt $elements.Count; $index++) {
        if ($elements[$index].Current.AutomationId -like '*OpenWith.exe') {
            $target = $elements[$index]
            break
        }
    }

    if ($null -eq $target) {
        Start-Sleep -Milliseconds 50
    }
}

if ($null -eq $target) {
    exit 1
}

$rectangle = $target.Current.BoundingRectangle
$original = New-Object OpenWithTaskbarClick+Point
[void][OpenWithTaskbarClick]::GetCursorPos([ref]$original)
$x = [int]($rectangle.X + $rectangle.Width / 2)
$y = [int]($rectangle.Y + $rectangle.Height / 2)

[void][OpenWithTaskbarClick]::SetCursorPos($x, $y)
[OpenWithTaskbarClick]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero)
[OpenWithTaskbarClick]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 80
[void][OpenWithTaskbarClick]::SetCursorPos($original.X, $original.Y)
