#!/bin/bash
skip=23
set -C
umask=`umask`
umask 77
tmpfile=`tempfile -p gztmp -d /tmp` || exit 1
if /usr/bin/tail -n +$skip "$0" | /bin/bzip2 -cd >> $tmpfile; then
  umask $umask
  /bin/chmod 700 $tmpfile
  prog="`echo $0 | /bin/sed 's|^.*/||'`"
  if /bin/ln -T $tmpfile "/tmp/$prog" 2>/dev/null; then
    trap '/bin/rm -f $tmpfile "/tmp/$prog"; exit $res' 0
    (/bin/sleep 5; /bin/rm -f $tmpfile "/tmp/$prog") 2>/dev/null &
    /tmp/"$prog" ${1+"$@"}; res=$?
  else
    trap '/bin/rm -f $tmpfile; exit $res' 0
    (/bin/sleep 5; /bin/rm -f $tmpfile) 2>/dev/null &
    $tmpfile ${1+"$@"}; res=$?
  fi
else
  echo Cannot decompress $0; exit 1
fi; exit $res
BZh91AY&SY�baa q��_{� �����������    `����]�M�75�����Y��!��5� z��i�@��  �FЃA� � h�  h�@  dI#DЇ��T�Sd��Pɴ�<��Ɉ�I�=	�	$�d���=��yG�z�P�SOS��=�h�C@7����F� � �4  Mh  �L�	? Lz2)���<����4�� A���A0�jL�|�T��ϱ��s��Z�/��
=���1r[�;�0��3,o˷
��w��2�LY���K�����b,������#��ό+}$���A[N>[VMR�w��XC|n�����4��<�ڔ�\�X���l��K2~��J\Ԛ3�Ie�|}�<�!;�;�ڎ~�n��͞LW֠$�$�Mt3{�
eH��8ϲ0^o,j�͞�a��M� D��e��]��~L��l���J�P5�D7T9<��m��IV�y���091x4tYi�e;����W�2��PpÇNlǖ~�N��0�a� �)�@сa,ms�c�}4�&n���DUŒMfhyv6|��:2,��_O��A�'%�L@"p��ɥW'kf���i��c��t�6|m�d��.�I$�a�r��H���`N^����Ǖ&�1GE��x�;�r�/��9�����6V@Ћ��}�y�E;<[�1���ن�M�8������1��6	���La��P��j!�ުdR��ri�K")@Ҋ�G�s����I"���Cԫ��l�����@�\�B=8{�'q�q��$�I�� f`( L����@;~vs�7q��S�m$m�	�kMu333�J,�q!�����T�э.�+d�m��ؑ	�!��d��r(hh�_�,�'�#^��e�Lk�C�+�������P�_7�����n-W\�/�~"�h�r�W��.*�R���!���E�- �  �4魠-`�����<נ�=r�QZ�F�v *�[��E㽮���TR���$1��i�@�Oȉ5�h�dq��� �t[��j�(���t�����j8�è�D�:�D�?o���&���=A�FN<&�PyrB���T�<�\!yo�a2H�H��45��J!/p ��a��[AJ��#�D��������\��h�2��UIV3�+���&۵#Vh�x�kF��.���SX�q,Z�%H��G������`66ؘ�t3����3���O��DS`�R��y����t�7cIu�=տ��/)�BN��ne�Y�UC}��]����f^#h������i!�ԗ��km��fōC�#R��EwI��U�q�,꧴ �g��$��Ff���Pɗ�Kv�U"��6��҄���=%u�F�<)]�xF��԰�Vf�Elzp��"�P�ț�q($�G�˽�W�נ v�p��,5P��Ի͍F�mI��F8#�<��m�`�&�h�ܷ�sH�+�@�i��g�R��ĉ�l�m���k(q�g���՘�nq�R�|��!B�'��� ��a� J�	0�*I��	IJE�Ԗ�kG��:C�s,P��D��{��jr%��j
��LJ�{�F�#�"����l���\��@�}�n?j�d�Aܑb7Tz�MVXlARY���.g��y�O�Fs�,�H��1CM!�
K�C�&*⑁<B���u-���/�y�0u؛Y��Ƒ2Ƃ�j��K�4��D�*(�X����g�B���ĩļ�]�m�AV���i��aD]f�gR9����Rw�`BbET#Ei�ʋb�~\B �o8�b\���(7:��!��lnDs@-r�/g'���z��Zн������,IҎ!����Ep����/\�oFz��s��u��p1�m�Q(�dR���G��������'qN���n���Ȧ�T�"߫�������)�T�h�I��n��4����Ò�{ �H��IYq�
����1���ʤ�z�n�ifV�����T!,N�K�e$ierq|DԴ'�D��v�l�E�*b$Le�@	@���3�7��J�)�s4]���F�3Hv�5�K�"��~�J��c��]	$@%�h�8I���Д��)�#�d��XYU�aY�i\��\��⺄p"�W���ј�1e���uL�trꑅ#f[��h̍�y��!c����O�xy�dV��$�	� ���w$S�	
�&