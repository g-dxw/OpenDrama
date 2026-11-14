"""Shared policy used by playback, unlock and client lock badges."""
def episode_access(drama, episode, *, member=False, unlocked=False):
    free_count = getattr(drama, 'non_member_free_episodes', None)
    if free_count is None:
        free_count = drama.free_episode_count or 0
    free = episode.episode_number <= free_count or getattr(episode, 'coin_price', None) == 0
    membership_required = getattr(drama, 'access_mode', 'public') == 'members' and not member and not free
    price = getattr(episode, 'coin_price', None)
    if price is None:
        price = drama.coin_price_per_episode or 0
    member_free = member and (not getattr(drama, 'member_pays_coins', False) or price == 0)
    return dict(free=free, allowed=not membership_required and (free or member_free or unlocked),membership_required=membership_required,coin_price=price)
