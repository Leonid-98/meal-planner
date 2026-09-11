#!/bin/sh
# Assembles the artboards (*.dc.html) from src/ fragments and _shared.css.
# Edit the fragments or the stylesheet, then run:  sh design/build.sh
set -e
cd "$(dirname "$0")"
hd() {
  printf '<!doctype html>\n<html>\n<head>\n  <meta charset="utf-8">\n  <script src="./support.js"></script>\n</head>\n<body>\n<x-dc>\n<helmet>\n  <style>\n'
  cat _shared.css
  [ -n "$1" ] && printf '%s\n' "$1"
  printf '  </style>\n</helmet>\n'
}
ft() { printf '</x-dc>\n</body>\n</html>\n'; }
cur() { [ "$1" = "$2" ] && printf current; return 0; }
topbar() {
  sed -e "s/@@W@@/$(cur "$1" W)/" -e "s/@@R@@/$(cur "$1" R)/" -e "s/@@I@@/$(cur "$1" I)/" -e "s/@@S@@/$(cur "$1" S)/" src/topbar.tpl.html
}
desk() { # out tab extra-css dark-class fragments... (dlg-* fragments render outside .shell as overlays)
  out=$1; tab=$2; css=$3; dark=$4; shift 4
  {
    hd "$css"
    printf '<div class="app%s"><div class="shell">\n' "$dark"
    topbar "$tab"
    for f in "$@"; do case "$f" in dlg-*) ;; *) cat "src/$f";; esac; done
    printf '</div>\n'
    for f in "$@"; do case "$f" in dlg-*) cat "src/$f";; esac; done
    printf '</div>\n'
    ft
  } > "$out"
}
phone() { out=$1; frag=$2; { hd ""; cat "src/$frag"; ft; } > "$out"; }

desk Main.dc.html         W "" ""      week-toolbar.html weekgrid.html
desk WeekDark.dc.html     W "body { background: #14161E; }" " dark" week-toolbar.html weekgrid.html
desk Picker.dc.html       W "" ""      week-toolbar.html weekgrid.html dlg-picker.html
desk FillWeek.dc.html     W "" ""      week-toolbar.html weekgrid.html dlg-fill.html
desk Settings.dc.html     W "" ""      week-toolbar.html weekgrid.html dlg-settings.html
desk Recipes.dc.html      R "" ""      page-recipes.html
desk RecipeEditor.dc.html R "" ""      page-recipe.html
desk Ingredients.dc.html  I "" ""      page-ingredients.html
desk ShoppingDesk.dc.html S "" ""      page-shopping.html
phone Today.dc.html     phone-today.html
phone Shopping.dc.html  phone-shopping.html
phone Cooking.dc.html   phone-cooking.html
phone WeekPhone.dc.html phone-week.html
{ hd ""; cat src/overview.html; ft; } > Overview.dc.html
echo "built $(ls *.dc.html | wc -l | tr -d ' ') artboards"
