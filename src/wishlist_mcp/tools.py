"""The twenty-four wishlist tools.

Parity is the rule: this exposes what the authenticated user can already do by
hand in the web app, with no agent-only privileges and nothing the app cannot do
either. Every tool maps onto an endpoint the website itself calls, so an agent
and a browser cannot reach different answers.

Parity does not hold on its own. The wishlist API ships from another repo on
another deploy, so an endpoint or a field can change there and reach the website
while this surface stays where it was. The ledger in the workspace repo,
docs/mcp-parity.md, is where that is tracked.

Docstrings here are the interface. They are what the calling model reads to
choose a tool, and they are the only guardrail on the write tools that
annotations do not already provide. Treat edits to them as interface changes.
"""

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_http_headers

from wishlist_mcp.auth import InvalidAccessToken, authenticate
from wishlist_mcp.client import WishlistAPI, segment
from wishlist_mcp.schemas import (
    AcceptedInvite,
    CircleMember,
    CircleRequest,
    DeletedItem,
    Dietary,
    GiftFormat,
    GiftGuide,
    GiftProfile,
    Interest,
    InviteOutcome,
    InvitePreview,
    Item,
    MyItem,
    MyProfile,
    Notification,
    NotificationFeed,
    Occasion,
    PendingRequests,
    Person,
    PriceComfort,
    Priority,
    ReadNotifications,
    Region,
    ReminderPreferences,
    RemovedMember,
    RequestOutcome,
    SettledRequest,
    UpcomingOccasions,
    Visibility,
)

# All four hints, spelled out on every tool. A missing hint is not neutral: the
# spec reads an absent destructiveHint as true, so a read tool that declares
# only readOnlyHint still advertises itself as destructive to a host that takes
# the default at its word.
READ = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
WRITE = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": False,
}
WRITE_IDEMPOTENT = {**WRITE, "idempotentHint": True}
# Not idempotent, deliberately: a second delete 404s, a second removal fails,
# and a second invite sends another email to a real person.
DESTRUCTIVE = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": False,
}

SEARCH_LIMIT = 20
# The calendar endpoint accepts 1-365. Ninety days is the default because it
# reaches the next occasion for almost anyone with a circle, without returning a
# year of dates the model has to skim past.
CALENDAR_DEFAULT_DAYS = 90
CALENDAR_MAX_DAYS = 365
NOTIFICATION_LIMIT = 50
NOTIFICATION_MAX_LIMIT = 200


def api() -> WishlistAPI:
    """A client carrying this caller's token.

    Verified here as well as by the wishlist API. This server has to check the
    audience itself, because a token minted for some other resource must not
    open this one, and only this server knows what its own resource URI is.
    """
    # get_http_headers() strips `authorization` by default, since forwarding it
    # downstream is usually wrong. Here it is exactly the point.
    header = get_http_headers(include={"authorization"}).get("authorization")
    try:
        authenticate(header)
    except InvalidAccessToken as exc:
        raise ToolError(
            f"Not signed in to wishlist: {exc.reason}. "
            "Reconnect the wishlist server in your client settings."
        ) from exc
    return WishlistAPI(header)


def register(mcp: FastMCP) -> None:
    """Attach every tool to the server."""

    # --- reads ---------------------------------------------------------------

    @mcp.tool(annotations=READ)
    def wishlist_search_people(query: str, limit: int = SEARCH_LIMIT) -> list[Person]:
        """Find people by username or display name. Start here when you know
        someone's name but not their wishlist username. Returns at most 20
        matches.

        Each match carries relationship, which says whether you are already in
        this person's circle, have a request outstanding either way, or can ask."""
        if not query.strip():
            return []
        data = api().get("/api/v1/search/people", {"q": query})
        results = (data or {}).get("results", [])
        capped = max(0, min(limit, SEARCH_LIMIT))
        return [_person(r) for r in results[:capped]]

    @mcp.tool(annotations=READ)
    def wishlist_get_gift_guide(username: str) -> GiftGuide:
        """Everything you need to choose a gift for one person: their sizes,
        birthday, interests, preferred brands and colours, price comfort,
        allergies, dietary rules, what they already own, things they do not want,
        and their wishlist.

        Use this first when the task is choosing a gift. It replaces calling
        wishlist_get_profile and wishlist_get_wishlist separately.

        Read allergies and dietary before suggesting anything consumable. They
        answer different questions: allergies is harm, dietary is a rule kept by
        choice, and neither implies the other."""
        client = api()
        profile = client.get(f"/api/v1/profiles/{segment(username)}")
        wishlist = client.get(f"/api/v1/wishlists/{segment(username)}")
        return GiftGuide(
            profile=GiftProfile(**_profile_fields(profile, GiftProfile)),
            items=[_item(i) for i in (wishlist or {}).get("items", [])],
            in_your_circle=bool((profile or {}).get("in_your_circle")),
        )

    @mcp.tool(annotations=READ)
    def wishlist_get_profile(username: str) -> GiftProfile:
        """One person's profile without their wishlist. Prefer
        wishlist_get_gift_guide unless you specifically do not want the items."""
        data = api().get(f"/api/v1/profiles/{segment(username)}")
        return GiftProfile(**_profile_fields(data, GiftProfile))

    @mcp.tool(annotations=READ)
    def wishlist_get_wishlist(username: str) -> list[Item]:
        """One person's wishlist without their profile. Prefer
        wishlist_get_gift_guide unless you specifically do not want the profile."""
        data = api().get(f"/api/v1/wishlists/{segment(username)}")
        return [_item(i) for i in (data or {}).get("items", [])]

    @mcp.tool(annotations=READ)
    def wishlist_get_my_profile() -> MyProfile:
        """Your own profile, including fields you have hidden from others, your
        birth year, and the visibility settings that hide them. Call this before
        wishlist_update_my_profile to see the seventeen visibility keys."""
        return _my_profile(api().get("/api/v1/profiles/me"))

    @mcp.tool(annotations=READ)
    def wishlist_get_my_wishlist() -> list[MyItem]:
        """Your own wishlist, including items marked circle_only. Use this to get
        item ids before updating or deleting an item."""
        data = api().get("/api/v1/wishlists/mine")
        return [_my_item(i) for i in (data or {}).get("items", [])]

    @mcp.tool(annotations=READ)
    def wishlist_list_circle() -> list[CircleMember]:
        """People in your circle. They can see the profile fields and wishlist
        items you have marked circle_only."""
        data = api().get("/api/v1/circle/members")
        return [
            CircleMember(
                username=m.get("username", ""), display_name=m.get("display_name")
            )
            for m in (data or {}).get("members", [])
        ]

    @mcp.tool(annotations=READ)
    def wishlist_list_circle_requests() -> PendingRequests:
        """Requests to join a circle that nobody has answered yet, in both
        directions.

        incoming is yours to answer with wishlist_accept_circle_request or
        wishlist_decline_circle_request. outgoing is what you are waiting on. Both
        carry the request id those tools need."""
        data = api().get("/api/v1/circle/requests")
        return PendingRequests(
            incoming=[_request(r) for r in (data or {}).get("incoming", [])],
            outgoing=[_request(r) for r in (data or {}).get("outgoing", [])],
        )

    @mcp.tool(annotations=READ)
    def wishlist_preview_invite(invite_token: str) -> InvitePreview:
        """See who sent an invite and whether it is still valid, before accepting
        it. The token is the long code at the end of an invite link."""
        data = api().get(f"/api/v1/invites/{segment(invite_token)}")
        invite = (data or {}).get("invite", {})
        return InvitePreview(
            inviter_username=invite.get("inviter_username", ""),
            inviter_display_name=invite.get("inviter_display_name"),
            state=invite.get("state", "expired"),
        )

    @mcp.tool(annotations=READ)
    def wishlist_upcoming_occasions(
        days: int = CALENDAR_DEFAULT_DAYS,
    ) -> UpcomingOccasions:
        """Birthdays and holidays coming up for you, soonest first. Start here for
        "whose birthday is next" or "what should I be shopping for".

        Birthdays are only the people whose circle you are in, and only those who
        filled a birthday in and left it announced. Someone missing from this list
        may still have a birthday you are not shown, so never tell the user that a
        person has none.

        Holidays follow the region in the user's reminder settings, which is why
        Mother's Day here may not be the date you would assume.

        A birthday carries the coming anniversary, never a year of birth. Pass a
        person's username to wishlist_get_gift_guide to turn a date into a gift."""
        window = max(1, min(days, CALENDAR_MAX_DAYS))
        data = api().get("/api/v1/calendar/upcoming", {"days": window}) or {}
        return UpcomingOccasions(
            from_date=data.get("from_date", ""),
            to_date=data.get("to_date", ""),
            occasions=[_occasion(o) for o in data.get("occasions", [])],
        )

    @mcp.tool(annotations=READ)
    def wishlist_list_notifications(
        unread_only: bool = False, limit: int = NOTIFICATION_LIMIT
    ) -> NotificationFeed:
        """Your in-app notifications: occasion reminders, invites people accepted,
        and circle requests.

        Reading them here does not mark them read. Use
        wishlist_mark_notifications_read for that, and only when the user asks."""
        capped = max(1, min(limit, NOTIFICATION_MAX_LIMIT))
        params = {"limit": capped}
        if unread_only:
            params["unread_only"] = "true"
        data = api().get("/api/v1/notifications", params) or {}
        return NotificationFeed(
            unread_count=int(data.get("unread_count") or 0),
            notifications=[_notification(n) for n in data.get("notifications", [])],
        )

    @mcp.tool(annotations=READ)
    def wishlist_get_reminder_preferences() -> ReminderPreferences:
        """How the user currently wants occasion reminders: whether email is on,
        how far ahead, which region's holidays, and whether their own birthday is
        announced to their circle.

        Read this before changing any of it, because
        wishlist_update_reminder_preferences replaces lead_days wholesale rather
        than adding to it."""
        data = api().get("/api/v1/reminders/preferences") or {}
        return _preferences(data.get("preferences", {}))

    # --- writes --------------------------------------------------------------

    @mcp.tool(annotations=WRITE)
    def wishlist_add_item(
        name: str,
        url: str | None = None,
        store_notes: str | None = None,
        priority: Priority | None = None,
        size: str | None = None,
        category: str | None = None,
        image_url: str | None = None,
        visibility: Visibility = "public",
    ) -> MyItem:
        """Add an item to your own wishlist. Only name is required.

        Use store_notes for the details that stop someone buying the wrong
        variant, such as colour, model, or which shop. Set visibility to
        circle_only to show the item to your circle and nobody else."""
        payload = _present(
            name=name,
            url=url,
            store_notes=store_notes,
            priority=priority,
            size=size,
            category=category,
            image_url=image_url,
            visibility=visibility,
        )
        return _my_item(api().post("/api/v1/wishlists/mine/items", payload))

    @mcp.tool(annotations=WRITE_IDEMPOTENT)
    def wishlist_update_item(
        item_id: int,
        name: str | None = None,
        url: str | None = None,
        store_notes: str | None = None,
        priority: Priority | None = None,
        size: str | None = None,
        category: str | None = None,
        image_url: str | None = None,
        visibility: Visibility | None = None,
    ) -> MyItem:
        """Change one of your own wishlist items. Omitted fields are left as they
        are. Get item_id from wishlist_get_my_wishlist."""
        payload = _present(
            name=name,
            url=url,
            store_notes=store_notes,
            priority=priority,
            size=size,
            category=category,
            image_url=image_url,
            visibility=visibility,
        )
        if not payload:
            raise ToolError("Nothing to change. Pass at least one field besides item_id.")
        return _my_item(api().patch(f"/api/v1/wishlists/mine/items/{item_id}", payload))

    @mcp.tool(annotations=DESTRUCTIVE)
    def wishlist_delete_item(item_id: int) -> DeletedItem:
        """Remove one of your own wishlist items. This cannot be undone, so confirm
        with the user which item they mean before calling. Get item_id from
        wishlist_get_my_wishlist."""
        api().delete(f"/api/v1/wishlists/mine/items/{item_id}")
        return DeletedItem(deleted=True, item_id=item_id)

    @mcp.tool(annotations=WRITE_IDEMPOTENT)
    def wishlist_update_my_profile(
        display_name: str | None = None,
        bio: str | None = None,
        pronouns: str | None = None,
        avatar_url: str | None = None,
        birth_date: str | None = None,
        shirt_size: str | None = None,
        shoe_size: str | None = None,
        pants_size: str | None = None,
        ring_size: str | None = None,
        preferred_brands: str | None = None,
        preferred_colors: str | None = None,
        gift_format_preference: GiftFormat | None = None,
        price_comfort: PriceComfort | None = None,
        interests: list[Interest] | None = None,
        allergies: str | None = None,
        dietary: list[Dietary] | None = None,
        address_line1: str | None = None,
        address_line2: str | None = None,
        city: str | None = None,
        state: str | None = None,
        zip_code: str | None = None,
        country: str | None = None,
        delivery_notes: str | None = None,
        things_i_dont_want: str | None = None,
        already_own: str | None = None,
        visibility: dict[str, Visibility] | None = None,
    ) -> MyProfile:
        """Change your own profile. Omitted fields are left as they are.

        birth_date is an ISO-8601 date, "1990-04-17". Nobody but you is ever shown
        the year.

        interests and dietary replace the whole list rather than adding to it, so
        read the current one with wishlist_get_my_profile first and send it back
        with your change folded in. At most 20 interests.

        visibility takes keys, not field names: {"address": "circle_only"} hides
        all six address fields and delivery_notes at once. Call
        wishlist_get_my_profile to see the seventeen keys it accepts."""
        payload = _present(
            display_name=display_name,
            bio=bio,
            pronouns=pronouns,
            avatar_url=avatar_url,
            birth_date=birth_date,
            shirt_size=shirt_size,
            shoe_size=shoe_size,
            pants_size=pants_size,
            ring_size=ring_size,
            preferred_brands=preferred_brands,
            preferred_colors=preferred_colors,
            gift_format_preference=gift_format_preference,
            price_comfort=price_comfort,
            interests=_tags(interests),
            allergies=allergies,
            dietary=dietary,
            address_line1=address_line1,
            address_line2=address_line2,
            city=city,
            state=state,
            zip_code=zip_code,
            country=country,
            delivery_notes=delivery_notes,
            things_i_dont_want=things_i_dont_want,
            already_own=already_own,
            visibility=visibility,
        )
        if not payload:
            raise ToolError("Nothing to change. Pass at least one field.")
        return _my_profile(api().patch("/api/v1/profiles/me", payload))

    @mcp.tool(annotations=WRITE)
    def wishlist_request_circle(username: str) -> RequestOutcome:
        """Ask someone who already has a wishlist account into your circle.

        They approve it in the app; no email link is sent. Nothing of yours is
        shared until they accept, and accepting is reciprocal, so tell the user
        that joining means this person will see the profile fields and wishlist
        items marked circle_only.

        If they had already asked you, this answers them instead and you both
        join at once. The outcome field says which happened.

        For an address that has no wishlist account behind it, use
        wishlist_create_invite."""
        data = api().post("/api/v1/circle/requests", {"username": username})
        return _request_outcome(data, fallback_username=username)

    @mcp.tool(annotations=WRITE_IDEMPOTENT)
    def wishlist_accept_circle_request(request_id: int) -> SettledRequest:
        """Accept a request and join that person's circle.

        This is reciprocal: they see the profile fields and wishlist items you
        have marked circle_only, as well as you seeing theirs. Tell the user that
        before calling, and name the person. Get request_id from
        wishlist_list_circle_requests."""
        data = api().post(f"/api/v1/circle/requests/{request_id}/accept")
        request = (data or {}).get("request", {})
        return SettledRequest(outcome="accepted", username=request.get("username", ""))

    @mcp.tool(annotations=WRITE_IDEMPOTENT)
    def wishlist_decline_circle_request(request_id: int) -> SettledRequest:
        """Decline a request. Nothing of yours is shared, and they cannot ask
        again for seven days. Get request_id from wishlist_list_circle_requests."""
        data = api().post(f"/api/v1/circle/requests/{request_id}/decline")
        request = (data or {}).get("request", {})
        return SettledRequest(outcome="declined", username=request.get("username", ""))

    @mcp.tool(annotations=DESTRUCTIVE)
    def wishlist_remove_circle_member(username: str) -> RemovedMember:
        """Remove someone from your circle.

        Membership is mutual, so this cuts both ways at once: they stop seeing
        what you marked circle_only, and you stop seeing what they marked
        circle_only. Getting back in means a fresh request that the other person
        has to accept. Confirm the name with the user before calling."""
        client, user_id = _resolve_member(username)
        client.delete(f"/api/v1/circle/members/{user_id}")
        return RemovedMember(removed=True, username=username)

    @mcp.tool(annotations=DESTRUCTIVE)
    def wishlist_create_invite(email: str) -> InviteOutcome:
        """Invite someone into your circle by email address.

        If nobody holds that address, this sends a real email immediately and it
        cannot be unsent. Read the address back to the user and get their
        confirmation before calling.

        If the address already belongs to a wishlist account, no email is sent:
        they get a request to approve in the app instead, exactly as if you had
        called wishlist_request_circle. Read the outcome field and tell the user
        which of the two happened rather than assuming an email went out.

        Anyone who accepts joins your circle, which lets them see every profile
        field and wishlist item you have marked circle_only."""
        data = api().post("/api/v1/invites", {"email": email})
        return _invite_outcome(data, email)

    @mcp.tool(annotations=WRITE_IDEMPOTENT)
    def wishlist_accept_invite(invite_token: str) -> AcceptedInvite:
        """Accept an invite and join that person's circle.

        This is reciprocal: they join your circle too, so accepting lets them see
        the profile fields and wishlist items you have marked circle_only. Tell the
        user that before calling. Use wishlist_preview_invite first to check who
        the invite is from."""
        data = api().post(f"/api/v1/invites/{segment(invite_token)}/accept")
        invite = (data or {}).get("invite", {})
        return AcceptedInvite(
            accepted=True, inviter_username=invite.get("inviter_username", "")
        )

    @mcp.tool(annotations=WRITE_IDEMPOTENT)
    def wishlist_update_reminder_preferences(
        email_enabled: bool | None = None,
        lead_days: list[int] | None = None,
        region: Region | None = None,
        birthday_reminders: bool | None = None,
        holiday_reminders: bool | None = None,
        announce_birthday: bool | None = None,
    ) -> ReminderPreferences:
        """Change how the signed-in user gets occasion reminders. Only the
        arguments you pass are changed.

        lead_days REPLACES the whole list rather than adding to it, so read
        wishlist_get_reminder_preferences first and send the full set you want.
        One to four values, each 0 to 60, where 0 means the day itself.

        announce_birthday is the one setting that acts on other people's mail:
        turning it off removes this user's birthday from every circle member's
        calendar. Confirm that with the user before changing it."""
        payload = {
            "email_enabled": email_enabled,
            "lead_days": lead_days,
            "region": region,
            "birthday_reminders": birthday_reminders,
            "holiday_reminders": holiday_reminders,
            "announce_birthday": announce_birthday,
        }
        changes = {k: v for k, v in payload.items() if v is not None}
        if not changes:
            raise ToolError(
                "Nothing to change. Pass at least one setting, or call "
                "wishlist_get_reminder_preferences to read the current ones."
            )
        data = api().patch("/api/v1/reminders/preferences", changes) or {}
        return _preferences(data.get("preferences", {}))

    @mcp.tool(annotations=WRITE_IDEMPOTENT)
    def wishlist_mark_notifications_read(
        notification_id: int | None = None,
    ) -> ReadNotifications:
        """Mark one notification read, or every unread one when you pass no id.

        Do this only when the user asks. Clearing someone's unread list as a side
        effect of reading it to them takes away the thing that told them there was
        something to look at."""
        client = api()
        if notification_id is None:
            data = client.post("/api/v1/notifications/read-all") or {}
            return ReadNotifications(marked_read=int(data.get("marked_read") or 0))
        client.post(f"/api/v1/notifications/{int(notification_id)}/read")
        return ReadNotifications(marked_read=1)


def _profile_fields(data: dict | None, model: type) -> dict:
    """The subset of an API profile the given schema carries.

    id and user_id are dropped: they are database keys with no meaning to a
    model, and username already identifies the person.
    """
    keep = set(model.model_fields)
    return {k: v for k, v in (data or {}).items() if k in keep}


def _my_profile(data: dict | None) -> MyProfile:
    fields = _profile_fields(data, MyProfile)
    # visibility is one of MyProfile's own fields, so it arrives in the filtered
    # dict already. A null from the API becomes {}, which reads as "all public".
    fields["visibility"] = fields.get("visibility") or {}
    return MyProfile(**fields)


def _person(data: dict) -> Person:
    keep = set(Person.model_fields)
    return Person(**{k: v for k, v in data.items() if k in keep})


def _item(data: dict) -> Item:
    keep = set(Item.model_fields)
    return Item(**{k: v for k, v in data.items() if k in keep})


def _my_item(data: dict | None) -> MyItem:
    data = data or {}
    keep = set(MyItem.model_fields)
    return MyItem(**{k: v for k, v in data.items() if k in keep})


def _request(data: dict) -> CircleRequest:
    keep = set(CircleRequest.model_fields)
    return CircleRequest(**{k: v for k, v in data.items() if k in keep})


def _request_outcome(data: dict | None, *, fallback_username: str) -> RequestOutcome:
    """Whether asking created a request or settled one that already existed.

    Discriminated on the request's own status rather than the HTTP status, which
    the client does not carry up. A request the API reports as already accepted
    is one the other person had opened first.
    """
    request = (data or {}).get("request", {})
    username = request.get("username") or fallback_username
    if request.get("status") == "accepted":
        return RequestOutcome(outcome="circle_joined", username=username)
    return RequestOutcome(
        outcome="request_sent", username=username, request_id=request.get("id")
    )


def _invite_outcome(data: dict | None, email: str) -> InviteOutcome:
    """Which of the two things POST /invites did.

    The endpoint answers `invite` for an address with no account behind it and
    `request` for one that has an account. Reading only `invite` is how this tool
    once reported a sent email for a request that emailed nobody.
    """
    body = data or {}
    if "request" in body:
        settled = _request_outcome(body, fallback_username="")
        return InviteOutcome(
            outcome=(
                "circle_joined" if settled.outcome == "circle_joined" else "request_sent"
            ),
            username=settled.username or None,
            request_id=settled.request_id,
        )

    invite = body.get("invite", {})
    return InviteOutcome(
        outcome="invite_emailed",
        email=invite.get("recipient_email", email),
        expires_at=(invite.get("expires_at") or "")[:10] or None,
    )


def _resolve_member(username: str) -> tuple[WishlistAPI, int]:
    """Turn a username into the id the circle endpoint deletes by.

    Every other tool addresses people by username, and swapping to a database id
    for one destructive call is how the wrong person gets removed. The lookup
    also means a name that is not in the circle fails before anything is deleted.
    """
    client = api()
    data = client.get("/api/v1/circle/members")
    for member in (data or {}).get("members", []):
        if member.get("username") == username:
            user_id = member.get("user_id")
            if user_id is None:
                break
            return client, user_id
    raise ToolError(
        f"{username} is not in your circle. Use wishlist_list_circle to see who is."
    )


# --- shaping -----------------------------------------------------------------


def _present(**kwargs) -> dict:
    """Only the fields the caller actually set.

    Sending an explicit null would clear a field the user never mentioned, which
    is how an agent quietly wipes someone's address. An empty list is kept: for
    dietary and interests it means "nothing applies", which is an answer.
    """
    return {k: v for k, v in kwargs.items() if v is not None}


def _tags(interests: list[Interest] | None) -> list[dict] | None:
    """Interests arrive as models and have to leave as JSON."""
    if interests is None:
        return None
    return [tag.model_dump() for tag in interests]


def _occasion(data: dict) -> Occasion:
    person = data.get("person")
    return Occasion(
        kind=data.get("kind", "holiday"),
        date=data.get("date", ""),
        days_away=int(data.get("days_away") or 0),
        title=data.get("title", ""),
        person=_person(person) if person else None,
    )


def _notification(data: dict) -> Notification:
    return Notification(
        id=int(data.get("id") or 0),
        kind=data.get("kind", "occasion_reminder"),
        title=data.get("title", ""),
        body=data.get("body"),
        read_at=data.get("read_at"),
        created_at=data.get("created_at"),
    )


def _preferences(data: dict) -> ReminderPreferences:
    return ReminderPreferences(
        email_enabled=bool(data.get("email_enabled", True)),
        lead_days=[int(d) for d in (data.get("lead_days") or [7, 1])],
        region=data.get("region", "US"),
        birthday_reminders=bool(data.get("birthday_reminders", True)),
        holiday_reminders=bool(data.get("holiday_reminders", True)),
        announce_birthday=bool(data.get("announce_birthday", True)),
    )
