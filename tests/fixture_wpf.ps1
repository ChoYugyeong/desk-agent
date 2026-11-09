# WPF test window for desk-agent (no child windows, no MSAA content: UI Automation only).
Add-Type -AssemblyName PresentationFramework
$xaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        Title="desk-test-wpf" Width="420" Height="320" Left="60" Top="60"
        WindowState="Minimized" ShowInTaskbar="False" ShowActivated="False">
  <StackPanel Margin="10">
    <TextBlock Name="Status" Text="idle"/>
    <Button Name="Hello" Content="Hello" Width="120" HorizontalAlignment="Left"/>
    <TextBox Name="Entry" Width="200" HorizontalAlignment="Left" Margin="0,8,0,0"/>
    <CheckBox Name="Flag" Content="Flag" Margin="0,8,0,0"/>
    <ComboBox Name="Pick" Width="120" HorizontalAlignment="Left" Margin="0,8,0,0">
      <ComboBoxItem Content="red"/><ComboBoxItem Content="green"/>
    </ComboBox>
    <Button Name="Del" Content="Delete all" Width="120" HorizontalAlignment="Left" Margin="0,8,0,0"/>
  </StackPanel>
</Window>
'@
$w = [Windows.Markup.XamlReader]::Parse($xaml)
$status = $w.FindName('Status')
$w.FindName('Hello').Add_Click({ $status.Text = 'clicked' })
$w.FindName('Del').Add_Click({ $status.Text = 'DELETED' })
[void]$w.ShowDialog()
