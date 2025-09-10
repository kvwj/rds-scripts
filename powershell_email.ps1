$recipients = "person1@kvwj.org,person2@kvwj.org"

$EmailFrom = "alertemail@kvwj.org"

$Subject = "Test Email from Powershell for BT80s"

$Body = "This is a test email for BT80s"

$SMTPServer = "smtp.gmail.com"

#$filenameAndPath = "testfile.txt"

$SMTPMessage = New-Object System.Net.Mail.MailMessage($EmailFrom,$recipients,$Subject,$Body)

#$attachment = New-Object System.Net.Mail.Attachment($filenameAndPath)

#$SMTPMessage.Attachments.Add($attachment)

$SMTPClient = New-Object Net.Mail.SmtpClient($SmtpServer, 587)

$SMTPClient.EnableSsl = $true

$SMTPClient.Credentials = New-Object System.Net.NetworkCredential("alertemail@kvwj.org", "password");

$SMTPClient.Send($SMTPMessage)
