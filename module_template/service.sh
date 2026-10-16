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

first_installation=$(get_conf_value "$CONFIG" "is_first_installation" "0")
multi_user=$(get_conf_value "$CONFIG" "is_multi_user" "0")
package_type=$(get_conf_value "$CONFIG" "package_type" "static")
initialization_ok=true
user_ids="0"

# 按安装选择确定用户范围。
if [ "$multi_user" = "1" ]; then
    if detected_users=$(ls /data/user/); then
        for user_id in $detected_users; do
            [ "$user_id" = "0" ] || user_ids="$user_ids $user_id"
        done
    else
        initialization_ok=false
        echo "- 读取用户列表失败"
    fi
fi

for user_id in $user_ids; do
    user_updated=false
    if [ "$first_installation" = "1" ] && [ "$user_id" != "0" ]; then
        if ! pm install-existing --user "$user_id" monet.com.tencent.wetype 2>/dev/null; then
            initialization_ok=false
            echo "- 用户 $user_id 安装 Overlay 失败"
            continue
        fi
        user_updated=true
    fi

    if [ "$package_type" = "dynamic" ]; then
        if ! cmd overlay enable --user "$user_id" monet.com.tencent.wetype 2>/dev/null; then
            initialization_ok=false
            echo "- 用户 $user_id 启用 Overlay 失败"
            continue
        fi
        user_updated=true
    fi

    if [ "$user_updated" = "true" ]; then
        am force-stop --user "$user_id" com.tencent.wetype 2>/dev/null
    fi
done

# 全部用户初始化成功后再清除重试状态。
if [ "$first_installation" = "1" ] && [ "$initialization_ok" = "true" ]; then
    set_conf_value "$CONFIG" "is_first_installation" "0"
fi