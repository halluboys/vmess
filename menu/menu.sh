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
BZh91AY&SY��u E�w� ���?�������`�  0  `�SS�{��ֽ����y� �� T2I�E=S�S�iM6��?LJS 2j4򇩵��4�� ��A)����꟪�d A�C��2 `�S�4����C  hb�C@A��A� 	
���`�~����ړ&Gꁠh~�� ���?Rd��h @44  4�4i�  C  $�M��S�ڃI�FM=PƧ���I�@ o X���m7��~���"�.��׊q;�Eq08
9�����s�jIpc���+k�Hц[�ն�
=�c��������Ɉ�H��� �4�$����Dpp"��:������R��5��ܪ�i����O�>�Qj�J�@�QW�Z�5Z���5�AE��*Efn.�v�����1�(&��v�����R�#]��  E�qu�U��3#"H4��]Uz�E9Pc������O:�4�����N�S��'��<�b��}�r�x��S^����f���U����-�иTj�}r���B�~�Ǜ?[��Аp�v_�;̌��@НM2"��A��G3U��h�����N�׆ѻ\vQe$�����A��֟� |	�Y�F �P-b����p�tI�dK �y�eI��-��+	��*�3�T�7@9���j
��wV�M��&�_�&0$Z!�!��P2\�&fB&�׈�H���(θ�΅�GEؗ+b�D2���&�i^�i+�|]9S�n ��hpp��"E��!��z:.�x��N�i�X�P�_�q�~|��D�d�KX�Rj�U2ש�1�"(8��c7GZA�hє��>G�V������@ )����H��0���_���~F5뜆�"����J�D��!ܨ�eC'�귱'�U�YŤ
 M�mD���/L�7�0=��q�4<����Em��Yf
��LN�:�g	��2�
�"L��yK�h5��^��&#�dK�F��'-�N�)�����r�˴�0i���U,XZLm�F!uJL�vR�oFߧ}�]m1�Sn �#�S��9'l��t��5����)��s������V�Ω{))%�qY���A�'�ᆳ��_��K-�ހ�3|�"��U.����ٝ7a�����YJ�������_��`	���A�n@F�Y���-�Dh���R�dP�C��T�*����̗���Hq��_8B�p�fhh����*j���.�NQ��.���&?_��d$@�C�R�K?��5r�hm>�!C.WU�c6�x��B �����P��x���B" ��#��Bl��O�{S�@DBy��)�y|��1�e�<��TV��
ms@<�b�"!̏a7:ш�Ґ~u�[�[�ͣl]�QǄw�J�&R1��Mo�d/��"Vn��_QgI��041'
=�9U"�u���3�,���s��7�k��A*8�E�3��Ɇ��n4������� <,&�qac; `d(�<�%Pe<��]sIZw�rJv�ʋ� r<p�e8��rZ6�s:�+
���s���z�,���3��V�"bmn�J*�Z���E {rs�iȋɻDB��<Ȋ-�v��em�~G^h!�f+���󣰺��q�UW�/DVSkSV�R#x�V.1��A�$W?f���ʢ����8H8���5�cy�3"�ɖ �W-����H�hf�&8n���������*�VwoI�"�2�Ȝ����Z�5����&�L0�e��Bb��c7�j�~d�2�����D(���><&���\m(lb�pPͥ�*�ƕ�h ��/I4�v\�#ZZ$�<��feh�[>u��[�D7�.Ƨt¬*�F�T�ͱ��jtC�aG9DG�^�$L�W�&R{(���-�(�(C>Z&�����g0�2�I0A�+�G!���	�E�1U1ex�ӬD��89�SgMz"�gx�� ��=�C�>q��g��렵Lz�:NWf	��F*bƆA[\AM��+��aR6�ӈ��"��Jt04�<"�&Z4�s�0S�����&D�;�5��E�����2�&����9�n��0+�`n��4�b��(&�2$�|��
�Fh��fL3$̯¨����#�%ŷL�\�V�7�.-H�~�6?�66�I�&����3���ջ;h��7��\�/n���ސ^��������<n�{���B�H������]��BB7��p