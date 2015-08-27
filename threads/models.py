from __future__ import unicode_literals

import urlman
import markdown
import urlparse

from django.db import models
from django.contrib.postgres.fields import ArrayField
from django_pgjson.fields import JsonBField


class Group(models.Model):
    """
    A grouping of threads, generally under some overarching subject like
    "Django" or "Kittens".
    """

    name = models.CharField(max_length=255, unique=True)
    created = models.DateTimeField(auto_now_add=True)
    active = models.BooleanField(default=True)
    colour = models.CharField(max_length=30, blank=True, null=True)

    class urls(urlman.Urls):
        view = "/g/{self.name}/"
        create_thread = "{view}t/create/"

    def get_absolute_url(self):
        return self.urls.view

    def __unicode__(self):
        return self.name

    @property
    def icon_colour(self):
        for char in self.colour.lower():
            if char not in "#1234567890abcdef":
                raise ValueError("Colour incorrect")
        return self.colour or "#369"


class GroupMember(models.Model):
    """
    Ties users into being members of Groups - that is, they see stuff from the
    groups on their homepage more often. This model also captures permissions
    that users can have on groups (admin, moderator, that kind of stuff).
    """

    PERMISSIONS = [
        "edit",
    ]

    group = models.ForeignKey(Group, related_name="members")
    user = models.ForeignKey("users.User", related_name="memberships")

    admin = models.BooleanField(default=False)
    moderator = models.BooleanField(default=False)

    def has_permission(self, permission):
        if permission not in self.PERMISSIONS:
            raise ValueError("Unknown permission %s" % permission)
        if self.admin:
            return True
        return False


class Thread(models.Model):
    """
    A thread is an overarching point of discussion. It could be a link, an image,
    or just a question or statement. They're highly moderated and selective; while
    users can suggest threads to a group, only a select group of users can
    actually approve them to appear in the main flow.

    Threads present as a sort of meld of chatroom and forum; there's single-level
    threading and longer form replies are encouraged, but at the same time
    replies appear instantly and can be composed inline.

    Whether this is a good idea remains to be seen.
    """

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]

    group = models.ForeignKey(Group, related_name="threads")
    author = models.ForeignKey("users.User", related_name="threads")

    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    status = models.CharField(max_length=30, choices=STATUS_CHOICES, db_index=True)

    score = models.FloatField(default=0, db_index=True)

    # A thread has at least a title, and might also have a link, text or
    # some other thing.
    title = models.TextField()
    url = models.URLField(blank=True, null=True)
    body = models.TextField(blank=True, null=True)

    class urls(urlman.Urls):
        view = "{self.group.urls.view}t/{self.id}/"
        content = "{self.content_url}"
        create_top_level_message = "{view}m/create/"

    def get_absolute_url(self):
        return self.urls.view

    def __unicode__(self):
        return self.title

    @property
    def content_url(self):
        """
        Returns the primary "content" url - an external URL if one provided,
        or the thread page if it's just text or other embedded content.
        """
        if self.url:
            return self.url
        return self.urls.view

    @property
    def content_domain(self):
        if self.url:
            try:
                bits = urlparse.urlparse(self.url)
                domain = bits.netloc
                if domain.startswith("www."):
                    domain = domain[4:]
                return domain
            except ValueError:
                pass
        return None

    @property
    def formatted_body(self):
        """
        Formats the body to HTML.
        """
        output = markdown.markdown(self.body, safe_mode=True, enable_attributes=False)
        # I'm really paranoid.
        if "<script" in output:
            raise ValueError("XSS attempt detected")
        return output

    @property
    def top_level_messages(self):
        """
        Returns all top-level (non-parented) messages
        """
        return self.messages.filter(
            parent__isnull=True
        ).annotate(
            num_children=models.Count('children')
        ).filter(
            models.Q(num_children__gt=0) | models.Q(deleted__isnull=True)
        )


class ThreadInteraction(models.Model):
    """
    When a thread is visited (the link clicked or the discussion page opened),
    entries are written into this model. A periodic task goes round and uses
    this to update the thread's current "score", along with inputs like
    "how much discussion is happening" and "how old is it?".

    This model isn't for permanent archival storage; old entries can be dropped
    after a while.
    """

    thread = models.ForeignKey(Thread, related_name="interactions")
    user = models.ForeignKey("users.User", related_name="interactions")
    view_content = models.BooleanField(default=False)
    view_discussion = models.BooleanField(default=False)
    when = models.DateTimeField(auto_now_add=True)


class Message(models.Model):
    """
    A message on a Thread. Can be either top-level or one level down;
    we disallow more nesting in the code, but the schema would technically
    allow full nesting.
    """

    thread = models.ForeignKey(Thread, related_name="messages", db_index=True)
    parent = models.ForeignKey("self", related_name="children", null=True, blank=True)

    author = models.ForeignKey("users.User", related_name="messages")

    body = models.TextField()

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    updated = models.DateTimeField(auto_now=True)
    edited = models.DateTimeField(null=True, blank=True)
    deleted = models.DateTimeField(null=True, blank=True)

    score = models.FloatField(default=0, db_index=True)

    class urls(urlman.Urls):
        view = "{self.thread.urls.view}m/{self.id}/"
        edit = "{view}edit/"
        delete = "{view}delete/"

    @property
    def formatted_body(self):
        """
        Formats the body to HTML.
        """
        output = markdown.markdown(self.body, safe_mode=True, enable_attributes=False)
        # I'm really paranoid.
        if "<script" in output:
            raise ValueError("XSS attempt detected")
        return output


class Reaction(models.Model):
    """
    A response to a message - either something positive, like a thanks or like,
    or something negative, like a spam or abuse report.
    """

    TYPE_CHOICES = [
        ("like", "Like"),
        ("informative", "Informative"),
        ("surprising", "Surprising"),
        ("confusing", "Confusing"),
        ("spam", "Spam"),
        ("abuse", "Abuse"),
    ]

    message = models.ForeignKey(Message, db_index=True)
    type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    user = models.ForeignKey("users.User", related_name="reactions")

    class Meta:
        unique_together = [
            ["message", "user"],
        ]
