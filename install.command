#!/bin/sh
package_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
/bin/sh "$package_dir/install.sh" --interactive "$@"
result=$?
if [ -t 0 ]; then
    printf '\n按回车关闭窗口…'
    read -r ignored
fi
exit "$result"
