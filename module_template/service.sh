#!/system/bin/sh
MODDIR=${0%/*}
CONFIG="$MODDIR/config.conf"
until [ "$(getprop sys.boot_completed)" = "1" ]; do
    sleep 2
done

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

# 多用户首次安装
if [ "$(get_conf_value "$CONFIG" "is_first_installation" "0")" = "1" ] && [ "$(get_conf_value "$CONFIG" "is_multi_user" "0")" = "1" ]; then
    for i in $(ls /data/user/ | awk 'NR>1'); do
        pm install-existing --user $i monet.com.tencent.wetype 2>/dev/null
        am force-stop --user $i com.tencent.wetype  2>/dev/null
    done
fi

# 启用 动态RRO
if [ $(get_conf_value "$CONFIG" "package_type" "static") = "dynamic" ];then
    user_id=$(ls /data/user/)
    for i in $user_id; do
        cmd overlay enable --user $i monet.com.tencent.wetype 2>/dev/null
        am force-stop --user $i com.tencent.wetype  2>/dev/null
    done
fi

# 首次安装后，设置为非首次安装
if [ "$(get_conf_value "$CONFIG" "is_first_installation" "0")" = "1" ];then
    set_conf_value "$CONFIG" "is_first_installation" "0"
fi