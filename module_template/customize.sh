#!/system/bin/sh
NAME="Wetype_Monet"
CONFIG="$MODPATH/config.conf"
OLD_CONFIG="/data/adb/modules/$NAME/config.conf"
PACKAGE_TYPE="dynamic"

author() {
    echo "
       ___        ____ _____      __ 
      <  /__  ___/ __ \___ /  ___/ / 
      / / _ \/ _  /_/ / |_ \ / __  /  
     /_/\___/\_,_/\____/____/\__,_/   
"
}

# 工具函数：监听音量键输入
listen_volume_key() {
  while :; do
    local key_input=$(getevent -qlc 1 | awk '{ print $3 }')
    case "$key_input" in
    KEY_VOLUMEUP) return 0 ;;
    KEY_VOLUMEDOWN) return 1 ;;
    *) continue ;;
    esac
  done
}

# 工具函数：读取配置项
# 参数：$1=配置文件路径, $2=键名(KEY), $3=默认值(可选)
get_conf_value() {
    local file="$1"
    local key="$2"
    local default="$3"
    
    [ ! -f "$file" ] && echo "$default" && return 0
    local value
    value=$(grep -E "^${key}=" "$file" | head -n1 | cut -d'=' -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
    if [ -n "$value" ]; then
        echo "$value"
    else
        echo "$default"
    fi
}

# 工具函数：写入/更新配置项
# 参数：$1=配置文件路径, $2=键名(KEY), $3=键值(VALUE)
set_conf_value() {
    local file="$1"
    local key="$2"
    local value="$3"
    
    mkdir -p "$(dirname "$file")"
    touch "$file"
    if grep -q -E "^${key}=" "$file"; then
        sed -i "s|^${key}=.*|${key}=\"${value}\"|" "$file"
    else
        echo "${key}=\"${value}\"" >> "$file"
    fi
}

# 检查安卓版本支持情况
check_support() {
    local sdk_ver=$(getprop ro.build.version.sdk)
    echo "- 正在检查支持情况……"
    if [ "$sdk_ver" -lt 34 ]; then
        abort "- 您的安卓版本不受支持（需要 Android 14 或以上）。"
    fi
}

# 备份旧配置文件
backup() {
    if [ -f "$OLD_CONFIG" ]; then
        cp -f "$OLD_CONFIG" "$CONFIG"
    fi
}

# 检查安装类型（动态模块或静态模块）
check_package_type() {
    if [ "$(get_conf_value "$CONFIG" "package_type" "static")" = "dynamic" ]; then
        PACKAGE_TYPE="dynamic"
    else
        PACKAGE_TYPE="static"
    fi
}

# 检查多用户环境、多用户安装
multi_user_installation() {
    if [ "$(get_conf_value "$CONFIG" "is_multi_user" "0")" = "0" ]; then
        if [ -n "$(ls /data/user/ | awk 'NR>1')" ]; then
            echo "- 检查到多用户环境"
            echo "- 是否为其他用户安装"
            echo "按音量键 ＋:   安装"
            echo "按音量键 －:  不安装"
            if listen_volume_key; then
                echo "- 已选择  安装"
                set_conf_value "$CONFIG" "is_multi_user" "1"
            else
                echo "- 已选择 不安装"
                set_conf_value "$CONFIG" "is_multi_user" "0"
            fi
        fi
    fi
}

# 安装逻辑
installation() {
    if [ "$PACKAGE_TYPE" = "dynamic" ] && [ "$(get_conf_value "$CONFIG" "is_first_installation" "1")" = "0" ]; then
        # 动态模块安装逻辑
        export MODULE_HOT_INSTALL_REQUEST=true
        install -r "$MODPATH/WetypeMonet.apk"
        am force-stop --user $i com.tencent.wetype  2>/dev/null
        echo "- [安装完成] 立即生效"
    else
        echo "- 正在进行静态模块安装……"
        # 静态模块安装逻辑
        mkdir -p "$MODPATH/system/priv-app/WetypeMonet"
        cp -rf "$MODPATH/files/WetypeMonet.apk" "$MODPATH/system/priv-app/WetypeMonet/WetypeMonet.apk"
        echo "- [安装完成] 重启生效"
    fi
}

tip() {
    set_perm_recursive "$MODPATH" 0 0 0755 0644
    echo "！！！！！！！ 记得看 ！！！！！！！"
    echo "- 若没有生效可排查: "
    echo "    1.是否安装元模块"
    echo "    2.是否关闭默认卸载模块"
    echo "！！！！！ 看完可以退出了 ！！！！！"
}

main() {
    author
    check_support
    check_package_type
    backup
    multi_user_installation
    installation
    tip
}
main