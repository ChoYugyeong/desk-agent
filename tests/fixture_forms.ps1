# WinForms test window for desk-agent (win32 controls + MSAA grid). Opens minimized so it does not take focus.
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object Windows.Forms.Form
$f.Text = 'desk-test-forms'
$f.Size = New-Object Drawing.Size(520, 420)
$f.StartPosition = 'Manual'
$f.Location = New-Object Drawing.Point(40, 40)
$f.WindowState = 'Minimized'
$f.ShowInTaskbar = $false

$label = New-Object Windows.Forms.Label
$label.Text = 'count=0'; $label.Location = New-Object Drawing.Point(10, 10); $label.AutoSize = $true
$count = New-Object Windows.Forms.Button
$count.Text = 'Count'; $count.Location = New-Object Drawing.Point(10, 40)
$count.Add_Click({ $script:n++; $label.Text = "count=$script:n" })
$script:n = 0

$box = New-Object Windows.Forms.TextBox
$box.Location = New-Object Drawing.Point(10, 80); $box.Width = 200

$agree = New-Object Windows.Forms.CheckBox
$agree.Text = 'Agree'; $agree.Location = New-Object Drawing.Point(10, 110)

$send = New-Object Windows.Forms.Button
$send.Text = 'Send report'; $send.Location = New-Object Drawing.Point(120, 40); $send.Width = 100
$send.Add_Click({ $label.Text = 'SENT' })

$grid = New-Object Windows.Forms.DataGridView
$grid.Location = New-Object Drawing.Point(10, 150); $grid.Size = New-Object Drawing.Size(480, 200)
$grid.AllowUserToAddRows = $false
[void]$grid.Columns.Add('item', 'Item'); [void]$grid.Columns.Add('qty', 'Qty')
[void]$grid.Rows.Add('apple', '3'); [void]$grid.Rows.Add('pear', '5'); [void]$grid.Rows.Add('plum', '7')

$f.Controls.AddRange(@($label, $count, $box, $agree, $send, $grid))
[Windows.Forms.Application]::Run($f)
