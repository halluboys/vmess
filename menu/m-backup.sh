#!/bin/bash
MYIP=$(wget -qO- ipinfo.io/ip);

echo -e "\e[33m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m"
echo -e "\E[0;100;33m       • BACKUP MENU •         \E[0m"
echo -e "\e[33m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m"
echo -e ""
echo -e " [\e[36m•1\e[0m] Add or Change Email Received "
echo -e " [\e[36m•2\e[0m] Change Email Sender  "
echo -e " [\e[36m•3\e[0m] Start Auto Backup "
echo -e " [\e[36m•4\e[0m] Stop Auto Backup "
echo -e " [\e[36m•5\e[0m] Backup Manualy "
echo -e " [\e[36m•6\e[0m] Test Send Mail "
echo -e " [\e[36m•7\e[0m] Restore "
echo -e ""
echo -e " [\e[31m•0\e[0m] \e[31mBACK TO MENU\033[0m"
echo -e ""
echo -e   "Press x or [ Ctrl+C ] • To-Exit"
echo ""
echo -e "\e[33m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m"
echo -e ""
read -p " Select menu :  "  opt
echo -e ""
case $opt in
1) clear ; addemail ; exit ;;
2) clear ; changesend ; exit ;;
3) clear ; startbackup ; exit ;;
4) clear ; stopbackup ; exit ;;
5) clear ; backup ; exit ;;
6) clear ; testsend ; exit ;;
7) clear ; restore ; exit ;;
0) clear ; menu ; exit ;;
x) exit ;;
*) echo "Anda salah tekan " ; sleep 1 ; m-sshovpn ;;
esac
