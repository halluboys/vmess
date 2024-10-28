#!/bin/bash

color1='\e[031;1m'
color2='\e[34;1m'
color3='\e[0m'
DF='\e[39m'
Bold='\e[1m'
Blink='\e[5m'
yell='\e[33m'
red='\e[1;31m'
green='\e[1;32m'
blue='\e[1;34m'
PURPLE='\e[1;95m'
CYAN='\e[1;36m'
Lred='\e[1;91m'
Lgreen='\e[92m'
Lyellow='\e[93m'
white='\e[1;37m'
NC='\e[0m'
tyblue='\e[1;36m'

MYIP=$(wget -qO- ifconfig.me/ip);
#########################
IZIN=$(curl -sS https://raw.githubusercontent.com/halluboys/perizinan/main/main/allow | awk '{print $4}' | grep $MYIP)
if [ $MYIP = $IZIN ]; then
echo -e "\e[32mPermission Accepted...\e[0m"
else
echo -e "\e[31mPermission Denied!\e[0m";
echo -e "\e[31mIJIN DULU NGENTOT!\e[0m"
exit 0
fi
#EXPIRED
expired=$(curl -sS https://raw.githubusercontent.com/halluboys/perizinan/main/main/allow | grep $MYIP | awk '{print $3}')
echo $expired > /root/expired.txt
today=$(date -d +1day +%Y-%m-%d)
while read expired
do
	exp=$(echo $expired | curl -sS https://raw.githubusercontent.com/halluboys/perizinan/main/main/allow | grep $MYIP | awk '{print $3}')
	if [[ $exp < $today ]]; then
		Exp2="\033[1;31mExpired\033[0m"
        else
        Exp2=$(curl -sS https://raw.githubusercontent.com/halluboys/perizinan/main/main/allow | grep $MYIP | awk '{print $3}')
	fi
done < /root/expired.txt
rm /root/expired.txt
Name=$(curl -sS https://raw.githubusercontent.com/halluboys/perizinan/main/main/allow | grep $MYIP | awk '{print $2}')
clear
# VPS Information
Checkstart1=$(ip route | grep default | cut -d ' ' -f 3 | head -n 1);
if [[ $Checkstart1 == "venet0" ]]; then
    clear
	  lan_net="venet0"
    typevps="OpenVZ"
else
    clear
		lan_net="eth0"
    typevps="KVM"
fi
clear

purple() { echo -e "\\033[35;1m${*}\\033[0m"; }
tyblue() { echo -e "\\033[36;1m${*}\\033[0m"; }
yellow() { echo -e "\\033[33;1m${*}\\033[0m"; }
green() { echo -e "\\033[32;1m${*}\\033[0m"; }
red() { echo -e "\\033[31;1m${*}\\033[0m"; }

#System version number
if [ "${EUID}" -ne 0 ]; then
		echo "You need to run this script as root"
		exit 1
fi
if [ "$(systemd-detect-virt)" == "openvz" ]; then
		echo "OpenVZ is not supported"
		exit 1
fi
clear
mkdir -p /etc/xray
mkdir -p /etc/v2ray
touch /etc/xray/domain
touch /etc/v2ray/domain
touch /etc/xray/scdomain
touch /etc/v2ray/scdomain

echo ""
#wget -q https://raw.githubusercontent.com/halluboys/vmess/main/tools.sh;chmod +x tools.sh;./tools.sh
#rm tools.sh
clear
red "Tambah Domain Untuk XRAY"
echo " "
read -rp "Input domain kamu : " -e dns
    if [ -z $dns ]; then
        echo -e "
        Nothing input for domain!
        Then a random domain will be created"
    else
        echo "$dns" > /root/scdomain
	echo "$dns" > /etc/xray/scdomain
	echo "$dns" > /etc/xray/domain
	echo "$dns" > /etc/v2ray/domain
	echo $dns > /root/domain
        echo "IP=$dns" > /var/lib/ipvps.conf
    fi
    
#Instal Xray
echo -e "\e[33m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m"
echo -e "$green          Install XRAY              $NC"
echo -e "\e[33m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\033[0m"
sleep 0.5
clear
wget https://raw.githubusercontent.com/halluboys/vmess/main/xray/ins-xray1.sh && chmod +x ins-xray1.sh && ./ins-xray1.sh
#wget https://raw.githubusercontent.com/halluboys/vmess/main/sshws/insshws.sh && chmod +x insshws.sh && ./insshws.sh
wget https://raw.githubusercontent.com/halluboys/vmess/main/backup/set-br.sh && chmod +x set-br.sh && ./set-br.sh

clear
cat> /root/.profile << END
# ~/.profile: executed by Bourne-compatible login shells.

if [ "$BASH" ]; then
  if [ -f ~/.bashrc ]; then
    . ~/.bashrc
  fi
fi

mesg n || true
clear
menu
END
chmod 644 /root/.profile

if [ -f "/root/log-install.txt" ]; then
rm /root/log-install.txt > /dev/null 2>&1
fi
if [ -f "/etc/afak.conf" ]; then
rm /etc/afak.conf > /dev/null 2>&1
fi
if [ ! -f "/etc/log-create-user.log" ]; then
echo "Log All Account " > /etc/log-create-user.log
fi
history -c
serverV=$( curl -sS https://raw.githubusercontent.com/halluboys/vmess/main/permission/versi  )
echo $serverV > /opt/.ver
aureb=$(cat /home/re_otm)
b=11
if [ $aureb -gt $b ]
then
gg="PM"
else
gg="AM"
fi
curl -sS ifconfig.me > /etc/myipvps
echo " "
echo "=====================-[ SUPREME ]-===================="
echo ""
echo "------------------------------------------------------------"
echo ""
echo ""
echo "   >>> Service & Port"  | tee -a log-install.txt
#echo "   - OpenSSH                  : 22"  | tee -a log-install.txt
#echo "   - SSH Websocket            : 80 [ON]" | tee -a log-install.txt
#echo "   - SSH SSL Websocket        : 443" | tee -a log-install.txt
#echo "   - Stunnel4                 : 222, 777" | tee -a log-install.txt
#echo "   - Dropbear                 : 109, 143" | tee -a log-install.txt
#echo "   - Badvpn                   : 7100-7900" | tee -a log-install.txt
#echo "   - Nginx                    : 81" | tee -a log-install.txt
echo "   - Vmess WS TLS             : 443" | tee -a log-install.txt
echo "   - Vless WS TLS             : 443" | tee -a log-install.txt
#echo "   - Trojan WS TLS            : 443" | tee -a log-install.txt
#echo "   - Shadowsocks WS TLS       : 443" | tee -a log-install.txt
echo "   - Vmess WS none TLS        : 80" | tee -a log-install.txt
echo "   - Vless WS none TLS        : 80" | tee -a log-install.txt
#echo "   - Trojan WS none TLS       : 80" | tee -a log-install.txt
#echo "   - Shadowsocks WS none TLS  : 80" | tee -a log-install.txt
#echo "   - Vmess gRPC               : 443" | tee -a log-install.txt
#echo "   - Vless gRPC               : 443" | tee -a log-install.txt
#echo "   - Trojan gRPC              : 443" | tee -a log-install.txt
#echo "   - Shadowsocks gRPC         : 443" | tee -a log-install.txt
#echo "   - TrojanGo                 : 2087" | tee -a log-install.txt
echo ""
echo ""
echo "------------------------------------------------------------"
echo ""
echo "=====================-[ SUPREME ]-===================="
echo -e ""
echo ""
echo "" | tee -a log-install.txt
rm /root/setup1.sh >/dev/null 2>&1
rm /root/ins-xray1.sh >/dev/null 2>&1
#rm /root/insshws.sh >/dev/null 2>&1
secs_to_human "$(($(date +%s) - ${start}))" | tee -a log-install.txt
echo -e "
"
echo -ne "[ ${yell}WARNING${NC} ] reboot now ? (y/n)? "
read answer
if [ "$answer" == "${answer#[Yy]}" ] ;then
exit 0
else
reboot
fi
