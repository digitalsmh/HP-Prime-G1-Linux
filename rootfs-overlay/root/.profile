# ~/.profile: executed by Bourne-compatible login shells.

# some more ls aliases
alias ll='ls -alF'
alias la='ls -A'
alias l='ls -CF'

# Hardware status aliases
alias hpp-batt='/usr/bin/hpp-batt'
alias hpp-mem='free -h'

# USB Memory aliases (/dev/sda4 ext4 <-> /mnt/usb)
alias usb-mount='mkdir -p /mnt/usb && mount -t ext4 /dev/sda4 /mnt/usb'
alias usb-umount='sync && umount /mnt/usb'
alias um='mkdir -p /mnt/usb && mount -t ext4 /dev/sda4 /mnt/usb'
alias uu='sync && umount /mnt/usb'

# Game aliases
alias lr='/usr/bin/hpp-loderunner'
alias loderunner='/usr/bin/hpp-loderunner'
alias xload='/root/70_xlode/run_xload.sh'
alias doom='/usr/bin/hpp-doom'
alias hpp-doom='/usr/bin/hpp-doom'

# Show Battery & Power Status on login
if [ -x /usr/bin/hpp-batt ]; then
    /usr/bin/hpp-batt
fi

# Show IP Address Information on login
echo ""
echo "=== IP Address (ip a) ==="
ip -4 a 2>/dev/null || ip a
echo "========================="
echo ""
echo "=== Quick Guide ==="
echo "  * Games    : lr (LodeRunner) / xload / doom (stop: <cmd> stop)"
echo "  * Hardware : hpp-batt (battery) / hpp-mem (memory)"
echo "  * USB Mount: usb-mount (um) -> mkdir -p /mnt/usb && mount -t ext4 /dev/sda4 /mnt/usb"
echo "  * USB Umnt : usb-umount (uu) -> sync && umount /mnt/usb"
echo "  * Keypad   : [Help]+[C]=Ctrl+C / [Menu]=KR Keyboard"
echo "  * Power    : [Shift]+[On]=Poweroff / [Alpha]+[On]=Reboot"
echo "==================="
echo ""

