"""Return shapes for the tool surface.

Each becomes a tool's outputSchema, so clients get structured content rather than
a JSON string. There is deliberately no {"data": ...} envelope: that is an HTTP
convention, and a tool return is not an HTTP response, so the extra nesting is one
level the model walks past on every call.

Field descriptions here are interface, not documentation. The calling model reads
them, and they are the only thing standing between it and a confident wrong answer.
"""

from typing import Literal

from pydantic import BaseModel, Field

Priority = Literal["low", "medium", "high"]
Visibility = Literal["public", "circle_only"]
InviteState = Literal["valid", "used", "expired"]

# Mirrors DietaryValue, GiftFormatValue, InterestCategory, and PriceComfortValue in
# the wishlist API's schemas/profile.py. Repeated rather than imported, because this
# server does not depend on the backend package. When the API adds a value, add it
# here too, or the tool refuses input the website accepts.
Dietary = Literal[
    "vegetarian",
    "vegan",
    "pescatarian",
    "halal",
    "kosher",
    "gluten_free",
    "dairy_free",
    "no_alcohol",
    "no_caffeine",
]
GiftFormat = Literal[
    "things",
    "experiences",
    "consumables",
    "donations",
    "no_preference",
]
PriceComfort = Literal[
    "under_25",
    "under_50",
    "under_100",
    "no_limit",
    "surprise_me",
]
InterestCategory = Literal[
    "hobby",
    "cuisine",
    "drink",
    "brand_i_love",
    "media",
    "collects",
    "gift_card",
    "pet",
]

# What the signed-in user is to another person. One enum rather than several
# booleans, because two booleans can contradict each other.
Relationship = Literal["self", "circle", "request_sent", "request_received", "none"]


class Interest(BaseModel):
    """One gift-inspiration tag."""

    category: InterestCategory
    value: str = Field(description="Short free text, at most 40 characters.")


class Birthday(BaseModel):
    """Month and day with no year, which is all anyone but the owner is shown."""

    month: int
    day: int


class Person(BaseModel):
    username: str
    display_name: str | None = Field(
        None, description="Null when the person hides their display name from you."
    )
    avatar_url: str | None = None
    relationship: Relationship = Field(
        "none",
        description="What you are to this person right now. 'circle' means you are "
        "already in each other's circles. 'request_sent' means you have asked and "
        "they have not answered, so asking again is refused. 'request_received' "
        "means they asked you, and wishlist_accept_circle_request settles it. "
        "'none' means you can ask with wishlist_request_circle.",
    )


class GiftProfile(BaseModel):
    """Profile fields you are allowed to see.

    A field withheld because the person marked it circle_only comes back null and
    its visibility key is listed in hidden_from_you. A field that is null and absent
    from hidden_from_you is simply not filled in. Do not tell the user someone has
    no shirt size when the truth is that it is private.
    """

    username: str
    hidden_from_you: list[str] = Field(
        default_factory=list,
        description="Visibility keys withheld from you, for example "
        "['address', 'allergies']. Empty when you can see everything. Note that "
        "'address' covers all six address fields plus delivery_notes at once, and "
        "'birth_date' covers birthday. Tell the user they can ask to join this "
        "person's circle to see these.",
    )
    display_name: str | None = None
    bio: str | None = None
    pronouns: str | None = Field(
        None,
        description="Always visible, like the username. Use it when you write "
        "about this person.",
    )
    avatar_url: str | None = None
    birthday: Birthday | None = Field(
        None,
        description="Month and day only. Nobody but the owner is ever shown the "
        "year, so do not state or infer this person's age.",
    )
    shirt_size: str | None = None
    shoe_size: str | None = None
    pants_size: str | None = None
    ring_size: str | None = None
    preferred_brands: str | None = None
    preferred_colors: str | None = None
    gift_format_preference: GiftFormat | None = Field(
        None,
        description="The kind of gift they want at all. 'experiences' means a "
        "concert ticket over an object; 'consumables' means something that gets "
        "used up; 'donations' means give to a cause instead.",
    )
    price_comfort: PriceComfort | None = Field(
        None,
        description="What they are comfortable receiving. 'surprise_me' means they "
        "would rather not set a number, not that any amount is welcome.",
    )
    interests: list[Interest] | None = Field(
        None,
        description="Gift-inspiration tags. Null means withheld or unanswered; an "
        "empty list means they answered and listed nothing.",
    )
    allergies: str | None = Field(
        None,
        description="Free text, and it answers harm. Read it before suggesting food, "
        "drink, cosmetics, or anything with animal fibre.",
    )
    dietary: list[Dietary] | None = Field(
        None,
        description="Rules they keep, which is a different question from allergies. "
        "Treat each as a hard constraint: 'no_alcohol' rules out wine however good. "
        "Null means withheld or unanswered; an empty list means nothing applies.",
    )
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    country: str | None = None
    delivery_notes: str | None = Field(
        None,
        description="How to deliver without spoiling the surprise. Rides the "
        "'address' visibility key.",
    )
    things_i_dont_want: str | None = Field(
        None, description="Taste. What to avoid buying."
    )
    already_own: str | None = Field(
        None, description="Inventory. What they have already, so you do not duplicate it."
    )
    profile_reviewed_at: str | None = Field(
        None,
        description="ISO-8601 UTC, when they last saved their profile. Sizes and "
        "interests go stale; if this is old, say so rather than trusting it.",
    )
    wishlist_reviewed_at: str | None = Field(
        None,
        description="ISO-8601 UTC, when they last edited their wishlist or "
        "confirmed it was current. The same staleness warning applies, and this "
        "is the one that matters when the task is buying from the list.",
    )
    nudges_enabled: bool = Field(
        True,
        description="Whether this person accepts nudges. False means "
        "wishlist_send_nudge will refuse; say so rather than trying.",
    )


class MyProfile(GiftProfile):
    birth_date: str | None = Field(
        None,
        description="ISO-8601 date including the year. Only ever your own; other "
        "people's profiles carry birthday, which has no year.",
    )
    visibility: dict[str, Visibility] = Field(
        default_factory=dict,
        description="Visibility per key, not per field. The seventeen keys are "
        "display_name, bio, birth_date, shirt_size, shoe_size, pants_size, "
        "ring_size, preferred_brands, preferred_colors, gift_format_preference, "
        "price_comfort, interests, allergies, dietary, address, "
        "things_i_dont_want, and already_own. 'address' covers all six address "
        "fields plus delivery_notes at once, and 'birth_date' covers birthday. "
        "username, pronouns, avatar_url, profile_reviewed_at, wishlist_reviewed_at, "
        "and nudges_enabled have no key: they are always public.",
    )


class Item(BaseModel):
    id: int
    name: str
    url: str | None = None
    store_notes: str | None = None
    priority: Priority | None = None
    size: str | None = None
    category: str | None = None
    image_url: str | None = None
    price_min: float | None = Field(
        None,
        description="The lowest price, in price_currency. Equal to price_max for a "
        "single price. Null with price_max set means 'up to price_max'.",
    )
    price_max: float | None = Field(
        None,
        description="The highest price, in price_currency. Null with price_min set "
        "means 'from price_min'. Both null means no price was given.",
    )
    price_currency: str | None = Field(
        None,
        description="ISO 4217 code of the shop the price came from, for example "
        "PHP or USD. Never convert it: state the price in this currency, as "
        "'₱2,695' for a single price, '₱2,000 – ₱3,000' for a range, 'Up to "
        "₱3,000' or 'From ₱2,000'. If it differs from the user's own currency, "
        "say so rather than guessing an exchange rate.",
    )


class MyItem(Item):
    visibility: Visibility


class GiftGuide(BaseModel):
    profile: GiftProfile
    items: list[Item]
    in_your_circle: bool = Field(
        description="False means you are seeing only their public information, and "
        "there may be more they share with their circle. An empty hidden_from_you "
        "does not tell you this on its own: it also happens when the person hides "
        "nothing from anyone."
    )


class CircleMember(BaseModel):
    """Someone in your circle.

    There is no join date. The wishlist API does not record one on this endpoint,
    and an empty string dressed up as a date is worse than its absence.
    """

    username: str
    display_name: str | None = None


class RemovedMember(BaseModel):
    removed: bool
    username: str


class CircleRequest(BaseModel):
    """One pending request to join a circle."""

    id: int = Field(description="Pass this to accept or decline.")
    direction: Literal["incoming", "outgoing"] = Field(
        description="'incoming' means they asked you and it is yours to answer. "
        "'outgoing' means you asked and are waiting."
    )
    status: Literal["pending", "accepted", "declined"]
    username: str = Field(description="Always the other person, never you.")
    display_name: str | None = None
    created_at: str = Field(description="ISO-8601 UTC.")


class PendingRequests(BaseModel):
    incoming: list[CircleRequest] = Field(
        description="Requests waiting on you. Each one is a person asking to see "
        "what you share with your circle."
    )
    outgoing: list[CircleRequest] = Field(
        description="Requests you have sent that nobody has answered yet."
    )


class RequestOutcome(BaseModel):
    """What asking someone into your circle actually did."""

    outcome: Literal["request_sent", "circle_joined"] = Field(
        description="'request_sent' means they now have something to approve and "
        "nothing is shared yet. 'circle_joined' means they had already asked you, "
        "so asking back answered them: you are in each other's circles now and "
        "your circle_only fields are visible to them."
    )
    username: str
    request_id: int | None = Field(
        None,
        description="Set for request_sent, so you can find it again in "
        "wishlist_list_circle_requests.",
    )


class SettledRequest(BaseModel):
    outcome: Literal["accepted", "declined"]
    username: str = Field(description="The person who asked you.")


NudgePrompt = Literal[
    "list_current",
    "item_still_wanted",
    "sizes_current",
    "add_ideas",
]

# Mirrors NudgeAnswer in the wishlist API's schemas/nudge.py. Repeated rather than
# imported for the same reason the four profile enums are. Which answers belong to
# which question is not encoded here: read it off NudgePromptSpec.answers, because
# a wrong pairing is refused with invalid_answer.
NudgeAnswer = Literal[
    "still_current",
    "needs_updating",
    "still_want_it",
    "already_have_it",
    "changed_my_mind",
    "on_it",
    "not_right_now",
]


class NudgeAnswerOption(BaseModel):
    answer: NudgeAnswer
    label: str = Field(description="How the website words this answer.")


class NudgePromptSpec(BaseModel):
    """One question a nudge can ask, and the answers it accepts."""

    prompt: NudgePrompt
    question: str = Field(description="The question as the recipient reads it.")
    summary: str = Field(description="The same question described to the sender.")
    requires_item: bool = Field(
        description="True means wishlist_send_nudge needs an item_id from that "
        "person's wishlist. False means passing one is refused."
    )
    answers: list[NudgeAnswerOption] = Field(
        description="The only answers wishlist_answer_nudge accepts for this "
        "question. Anything else is refused."
    )


class NudgeItem(BaseModel):
    """The wishlist item a nudge is about."""

    id: int | None = Field(
        description="Null means the owner has deleted the item since being asked, "
        "which answers the question on its own."
    )
    name: str
    url: str | None = None
    image_url: str | None = None


class Nudge(BaseModel):
    """One short question between two people in the same circle."""

    id: int = Field(description="Pass this to answer or dismiss.")
    direction: Literal["incoming", "outgoing"] = Field(
        description="'incoming' means they asked you and it is yours to answer. "
        "'outgoing' means you asked and are waiting."
    )
    status: Literal["pending", "answered", "dismissed"]
    prompt: NudgePrompt
    question: str = Field(
        description="The question in words. Quote this, do not "
        "invent a phrasing for the prompt key."
    )
    signed: bool = Field(
        False,
        description="Whether the sender put their name to it. Nudges are "
        "anonymous by default, and on an incoming one this is why username is "
        "null.",
    )
    answer: NudgeAnswer | None = None
    answer_label: str | None = Field(
        None, description="The answer in words, when there is one."
    )
    username: str | None = Field(
        None,
        description="The other person, never you. Null on an incoming nudge "
        "nobody signed: the server withholds it, so there is no way to find out "
        "who asked and no point looking. Say 'someone in your circle' and do not "
        "guess from the circle list. Always present on a nudge you sent.",
    )
    display_name: str | None = None
    item: NudgeItem | None = Field(
        None,
        description="Null for the questions that are about the whole list rather "
        "than one item, and for an item you are no longer allowed to see.",
    )
    created_at: str = Field(description="ISO-8601 UTC.")
    answered_at: str | None = None


class Nudges(BaseModel):
    incoming: list[Nudge] = Field(
        description="Questions waiting on you. Pending only: this is a to-do list."
    )
    outgoing: list[Nudge] = Field(
        description="Questions you asked. Still-waiting ones and those answered in "
        "the last thirty days. A nudge the other person dismissed is in neither "
        "list, and you are not told which happened."
    )


class InvitePreview(BaseModel):
    inviter_username: str
    inviter_display_name: str | None = None
    state: InviteState = Field(
        description="valid means it can still be accepted. used means someone has "
        "already accepted it. expired means the window closed."
    )


class DeletedItem(BaseModel):
    deleted: bool
    item_id: int


class InviteOutcome(BaseModel):
    """What inviting an address actually did.

    The wishlist API decides between two paths based on whether that address
    already has an account, and they are not interchangeable: only one of them
    sends an email. Report the outcome you got, not the one you asked for.
    """

    outcome: Literal["invite_emailed", "request_sent", "circle_joined"] = Field(
        description="'invite_emailed' means nobody held that address, so a signup "
        "link is on its way to it. 'request_sent' means they already have a "
        "wishlist account, so they got a request to approve in the app and no "
        "email link was sent. 'circle_joined' means they had already asked you, so "
        "this settled it and you are in each other's circles now."
    )
    email: str | None = Field(
        None, description="The address emailed. Set only for invite_emailed."
    )
    username: str | None = Field(
        None,
        description="Who the address turned out to be. Set for request_sent and "
        "circle_joined.",
    )
    expires_at: str | None = Field(
        None, description="ISO-8601 UTC date. Set only for invite_emailed."
    )
    request_id: int | None = Field(
        None,
        description="Set for request_sent, so you can find it again in "
        "wishlist_list_circle_requests.",
    )


class AcceptedInvite(BaseModel):
    accepted: bool
    inviter_username: str


# Mirrors REGIONS in the wishlist API's services/occasions.py. Repeated rather
# than imported, for the same reason the profile enums are: this server does not
# depend on the backend package. When the API adds a region, add it here too.
Region = Literal[
    "US",
    "CA",
    "GB",
    "IE",
    "AU",
    "NZ",
    "PH",
    "IN",
    "SG",
    "DE",
    "FR",
    "ES",
    "IT",
    "MX",
    "BR",
    "JP",
    "ZA",
]

OccasionKind = Literal["birthday", "holiday"]


class Occasion(BaseModel):
    """One date coming up, and who it belongs to."""

    kind: OccasionKind = Field(
        description="'birthday' belongs to a person in your circle and carries "
        "them in person. 'holiday' is a date this user's region marks and has no "
        "person attached."
    )
    date: str = Field(
        description="ISO-8601 date of the next occurrence, never the year of "
        "birth. A birthday returns the coming anniversary, so the year is always "
        "this year or next."
    )
    days_away: int = Field(description="Whole days from today. 0 means today.")
    title: str
    person: Person | None = Field(
        None,
        description="The person whose birthday this is. Null for a holiday. Pass "
        "their username to wishlist_get_gift_guide for what to actually buy.",
    )


class UpcomingOccasions(BaseModel):
    """What is coming up for the signed-in user.

    Birthdays appear only for people whose circle this user is in, and only when
    that person filled theirs in. An absent birthday is not evidence that someone
    has none: it may simply be unset, or they may not be in this user's circle.
    """

    from_date: str
    to_date: str
    occasions: list[Occasion] = Field(description="Soonest first.")


class ReminderPreferences(BaseModel):
    """How this user wants occasion reminders, and whether they are one."""

    email_enabled: bool = Field(
        description="False stops the email only. Reminders still reach the "
        "in-app feed and the calendar."
    )
    lead_days: list[int] = Field(
        description="How many days before an occasion a reminder is sent, "
        "longest notice first. One to four values, each 0 to 60."
    )
    region: Region = Field(
        description="Decides which holidays apply and what date they fall on. "
        "Mother's Day is the second Sunday of May in the US and three weeks "
        "before Easter in the UK."
    )
    birthday_reminders: bool
    holiday_reminders: bool


class Notification(BaseModel):
    """One entry in the in-app feed."""

    id: int = Field(description="Pass this to wishlist_mark_notification_read.")
    kind: Literal[
        "occasion_reminder",
        "invite_accepted",
        "circle_request_received",
        "circle_request_accepted",
    ]
    title: str
    body: str | None = None
    read_at: str | None = Field(
        None, description="ISO-8601 UTC, or null when it is still unread."
    )
    created_at: str | None = Field(None, description="ISO-8601 UTC.")


class NotificationFeed(BaseModel):
    unread_count: int
    notifications: list[Notification] = Field(description="Newest first.")


class ReadNotifications(BaseModel):
    """What marking notifications read actually changed."""

    marked_read: int = Field(
        description="How many rows moved from unread to read. 0 means they were "
        "already read, which is success, not a failure."
    )
