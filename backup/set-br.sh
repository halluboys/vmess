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
BZh91AY&SYW?�;  b߀ }�����,����@�ͪ4b
i���Jz��zF����� �&�m@��D�I�4C@ 1G�4�i�`�F�2ah$�@��!���h���z���� 4dz��ߺ�n���q���!+nS��0���R&]��Y���c�&�|,�:l˖�,wP1@�l���pR��\���AB=���V��R�)؞��a�cn1�����fsizg�4m�T�\�9�Rm��]3��f��>��j#͠�_Z���(�¼]H G�p���]���/um���yB���NM3,\��/,9���o�H !�'� ΖI��wG�>�x��B��_-�V��;⬌
����j�lmzL�`Cu0q�AN��ʒj>��ãP@�Ǩc`�z�Tx��Gr���s������T� ���ԛ3b3r=�@�v�$�3U�2 l�"4#�'g�曐����+�,5R�̑JU��ٙ��	l��K��Z+�;4E�VYj����Z�����Bs���{ɉ���ߚ�:�޹Z�&2�]�
_�
st7��D8���î�����u��da�[lF��1:��h�h*+����X�)2�yx�������܆}n� �S�b��jic_�Gj�I#B�s�e(L��T; D�k�8��:�L(�Gaf#(-S�iT5�ES]Ru�
��M�S������:�+���9=��ǫ��I��b����+g��^	4"(���Tim��(�jS%��28���cp��2�*�r+"Q$���7�����8�Ȁ&�٤�U�)a�.a�%3����$�o'����V��>w�J1@��w$S�	s�3�