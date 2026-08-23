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


class Person(BaseModel):
    username: str
    display_name: str | None = Field(
        None, description="Null when the person hides their display name from you."
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
        "'address' covers all six address fields at once. Tell the user they can "
        "ask to join this person's circle to see these.",
    )
    display_name: str | None = None
    bio: str | None = None
    shirt_size: str | None = None
    shoe_size: str | None = None
    pants_size: str | None = None
    preferred_brands: str | None = None
    preferred_colors: str | None = None
    allergies: str | None = None
    things_i_dont_want: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    country: str | None = None


class MyProfile(GiftProfile):
    visibility: dict[str, Visibility] = Field(
        default_factory=dict,
        description="Visibility per key, not per field. The ten keys are "
        "display_name, bio, shirt_size, shoe_size, pants_size, preferred_brands, "
        "preferred_colors, allergies, things_i_dont_want, and address, where "
        "address covers all six address fields at once. Keys absent here are public.",
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
    username: str
    display_name: str | None = None
    since: str = Field(description="ISO-8601 UTC date they joined your circle.")


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


class CreatedInvite(BaseModel):
    email: str
    state: Literal["sent"]
    expires_at: str = Field(description="ISO-8601 UTC.")


class AcceptedInvite(BaseModel):
    accepted: bool
    inviter_username: str
