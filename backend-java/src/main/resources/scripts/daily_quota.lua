-- KEYS[1] quota key
-- ARGV[1] daily limit
-- ARGV[2] ttl seconds
-- Returns current count when allowed, -1 when quota is exceeded.
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local ttl_seconds = tonumber(ARGV[2])

local current = redis.call('INCR', key)
if current == 1 then
    redis.call('EXPIRE', key, ttl_seconds)
end

if current > limit then
    return -1
end
return current
