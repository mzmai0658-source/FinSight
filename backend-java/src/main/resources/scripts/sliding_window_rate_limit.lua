-- 滑动窗口限流（ZSET 实现）
-- KEYS[1] 限流 key
-- ARGV[1] 窗口毫秒数
-- ARGV[2] 窗口内允许的最大请求数
-- ARGV[3] 当前时间戳（毫秒）
-- 返回: 1 允许, 0 拒绝
local key = KEYS[1]
local window = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local current = redis.call('ZCARD', key)
if current >= limit then
    return 0
end
redis.call('ZADD', key, now, tostring(now) .. '-' .. tostring(math.random(1000000)))
redis.call('PEXPIRE', key, window + 1000)
return 1
