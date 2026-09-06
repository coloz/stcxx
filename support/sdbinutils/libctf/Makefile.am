## Process this file with automake to produce Makefile.in.
#
#   Copyright (C) 2019-2026 Free Software Foundation, Inc.
#
# This file is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; see the file COPYING.  If not see
# <http://www.gnu.org/licenses/>.
#

ACLOCAL_AMFLAGS = -I .. -I ../config -I ../bfd

AUTOMAKE_OPTIONS = foreign info-in-builddir no-texinfo.tex

# Suppress 'configure.ac: error: AM_GNU_GETTEXT used but SUBDIRS not defined'
# from Automake, as libctf uses AM_GNU_GETTEXT (through
# ZW_GNU_GETTEXT_SISTER_DIR) but has no po/ subdir (currently).
SUBDIRS =

# Variables that we might accumulate conditionally or in subdirs.
info_TEXINFOS =
DISTCLEANFILES =
MAINTAINERCLEANFILES =

# This is where we get zlib from.  zlibdir is -L../zlib and zlibinc is
# -I../zlib, unless we were configured with --with-system-zlib, in which
# case both are empty.
ZLIB = @zlibdir@ -lz
ZLIBINC = @zlibinc@

BASEDIR = $(srcdir)/..
BFDDIR = $(BASEDIR)/bfd
INCDIR = $(srcdir)/../include
AM_CPPFLAGS = -D_GNU_SOURCE -I$(srcdir) -I$(srcdir)/../include -I$(srcdir)/../bfd -I../bfd @INCINTL@
AM_CFLAGS = -std=gnu99 @ac_libctf_warn_cflags@ @warn@ @c_warn@ @WARN_PEDANTIC@ @WERROR@ $(ZLIBINC)

if INSTALL_LIBBFD
lib_LTLIBRARIES = libctf.la libctf-nobfd.la
include_HEADERS = $(INCDIR)/ctf.h $(INCDIR)/ctf-api.h
else
include_HEADERS =
noinst_LTLIBRARIES = libctf.la libctf-nobfd.la
endif

libctf_nobfd_la_LIBADD = @CTF_LIBADD@ $(ZLIB)
libctf_ldflags_nover = -version-info 0:0:0 @SHARED_LDFLAGS@
libctf_nobfd_la_LDFLAGS = $(libctf_ldflags_nover) @VERSION_FLAGS_NOBFD@
libctf_nobfd_la_CPPFLAGS = $(AM_CPPFLAGS) -DNOBFD=1
libctf_nobfd_la_SOURCES = ctf-archive.c ctf-dump.c ctf-create.c ctf-decl.c ctf-error.c \
			  ctf-hash.c ctf-labels.c ctf-dedup.c ctf-link.c ctf-lookup.c \
			  ctf-open.c ctf-serialize.c ctf-sha1.c ctf-string.c ctf-subr.c \
			  ctf-types.c ctf-util.c
if NEED_CTF_QSORT_R
libctf_nobfd_la_SOURCES += ctf-qsort_r.c
endif

# @CTF_LIBADD@ appears here twice: once, to force libiberty to get searched before
# install-time libbfd adds a -L to the install dir (possibly pointing at an
# incompatible libiberty), and once via lictf_nobfd_la_LIBADD so that the
# references in there get picked up.
libctf_la_LIBADD =  @CTF_LIBADD@ ../bfd/libbfd.la $(libctf_nobfd_la_LIBADD)
libctf_la_CPPFLAGS = $(AM_CPPFLAGS) -DNOBFD=0
libctf_la_LDFLAGS = $(libctf_ldflags_nover) @VERSION_FLAGS@
libctf_la_SOURCES = $(libctf_nobfd_la_SOURCES) ctf-open-bfd.c

# development.sh is used to determine -Werror default.
CONFIG_STATUS_DEPENDENCIES = $(BFDDIR)/development.sh

include doc/local.mk
