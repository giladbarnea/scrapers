Title: How to Add and Remove a Verified Phone Number or Caller ID with Twilio - Twilio Help Center

URL Source: https://help.twilio.com/articles/223180048-How-to-Add-and-Remove-a-Verified-Phone-Number-or-Caller-ID-with-Twilio

Markdown Content:
Objective
---------

An existing non-Twilio phone number, like the number to a wireless phone or a landline in your home or office, must be validated on your Twilio account, via a One-time-passcode (OTP), before it can be used for specific Twilio account services.

*   For [free trial accounts](https://help.twilio.com/articles/223136107) you will only be able to make calls or send SMS from your free trial account to numbers that are verified caller IDs on the account. Additionally, you will only be able to receive inbound calls from numbers that are verified caller IDs on the account. We allow verified phone numbers to be used as a caller ID on outbound calls for trial accounts as well. For a list of the limitations you may run into, see[Twilio Free Trial Limitations](https://help.twilio.com/articles/360036052753-Twilio-Free-Trial-Limitations).
*   For [upgraded accounts](https://help.twilio.com/articles/223183208) we allow verified phone numbers to be used as a caller ID on outbound calls (this feature not available for outbound messaging).

**NOTE:** Verified Caller IDs cannot be used as the display name on outgoing SMS or MMS message. Verified Caller IDs are intended to be used with voice services only.

While a verified phone number can be used as a Caller ID for outbound Twilio calls, you will not be able to receive inbound calls to Twilio over this number. Incoming calls to the verified number will continue to route through the existing service provider (your wireless service, landline provider, etc.). If you would like to receive incoming calls through Twilio, you may be able to forward your calls to a Twilio phone number via your service provider. Alternatively, we may be able to port your phone number in. For more information, please see our article[Porting a Phone Number to Twilio](https://help.twilio.com/articles/223179348-Porting-a-Phone-Number-to-Twilio).

Product
-------

Programmable Voice

Procedure
---------

### Add a Verified Phone Number via Console[🔗](https://help.twilio.com/articles/223180048-How-to-Add-and-Remove-a-Verified-Phone-Number-or-Caller-ID-with-Twilio#h_01GQT9YZMY444KNH3M5AK065GX)

(warning)

**Notice:** If you use our [Twilio Regions](https://www.twilio.com/docs/global-infrastructure/understanding-twilio-regions) data routing services the Verified Caller ID service is only available for US1 data routing region at this time. It is not available for IE1 or AU1 data routing regions. You can still add Ireland and Australia numbers as caller IDs to the US1 data route. Please see [Regional product availability](https://www.twilio.com/docs/global-infrastructure/regional-product-and-feature-availability) for more information on services supported for specific regional data routing.

1.   Access the [**Verified Caller IDs** page in Console](https://www.twilio.com/console/phone-numbers/verified).
2.   Click **Add a new Caller ID**![Image 1: addCallerID_01_113px.png](https://support.twilio.com/hc/article_attachments/4407842845211).
3.   Enter the desired phone number to verify, select the desired verification method, and then click **Verify Number**.

![Image 2: Screenshot_2023-01-26_at_11.36.07_AM.png](https://support.twilio.com/hc/article_attachments/12372315574811)
4.   The number entered will receive an OTP Authentication code for verification. Enter this verification code on the next window.

![Image 3](https://support.twilio.com/hc/article_attachments/24214754075035)
5.   Once you click **Submit**, if the correct OTP code was entered, you will receive a **Successful** notification and the number will be added to your account as a verified caller ID.

![Image 4](https://support.twilio.com/hc/article_attachments/24214754089243)

(information)

**Notice:** The Verified Caller ID Friendly Name must be an alpha numeric value and less than 64 characters in length. Any characters provided over 64 will be excluded from the friendly name when saved. Only the first 64 characters will be saved and set as the friendly name.

### Add a Verified Phone Number via the REST API [🔗](https://help.twilio.com/articles/223180048-How-to-Add-and-Remove-a-Verified-Phone-Number-or-Caller-ID-with-Twilio#h_01GQT9Z7A14AE5K96SPXYPCDN1)

A verified phone number can be added to your account by submitting an **HTTP POST** request to the `OutgoingCallerIds` REST API resource. For full details, please see [Add an Outgoing Caller ID (Twilio Docs)](https://www.twilio.com/docs/voice/api/outgoing-caller-ids#add-an-outgoing-caller-id).

### Verifying Phone Numbers Behind an IVR or Extension [🔗](https://help.twilio.com/articles/223180048-How-to-Add-and-Remove-a-Verified-Phone-Number-or-Caller-ID-with-Twilio#h_01JC6QWAXEKD4ZH103F775EMCG)

You can verify numbers behind IVRs using both the above methods by including `w` characters in the Extension parameter. Each `w` character tells Twilio to wait 0.5 seconds instead of playing a digit. This lets you adjust the timing of when the digits begin playing to suit the phone system you are dialing.

For example, the extension `wwww2wwwwww5wwwwww9` waits two seconds before sending the digit 2, followed by a three second wait before sending the 5, and finally another three second wait before sending a 9. You will need to tune the extension to the IVR system to be able to programmatically verify numbers behind IVR menus.

### Verifying Phone Numbers at Scale [🔗](https://help.twilio.com/articles/223180048-How-to-Add-and-Remove-a-Verified-Phone-Number-or-Caller-ID-with-Twilio#h_01JC6QWAXEB12N77EX9GX5K4WN)

### Remove a Verified Phone Number via Console [🔗](https://help.twilio.com/articles/223180048-How-to-Add-and-Remove-a-Verified-Phone-Number-or-Caller-ID-with-Twilio#h_01JC6QWAXESCXCFE5KV5T8PMQ2)

1.   Access the [**Verified Caller IDs** page in Console](https://www.twilio.com/console/phone-numbers/verified).
2.   Locate the desired phone number to remove.
3.   To remove a Verified Caller ID, click **Remove**![Image 5: Screenshot_2023-01-26_at_11.15.55_AM.png](https://support.twilio.com/hc/article_attachments/12371871847323)

Additional Information
----------------------

*   [How does Twilio's Free Trial work?](https://help.twilio.com/articles/223136107-How-does-Twilio-s-Free-Trial-work-)
*   [Using a non-Twilio number as the caller ID for outgoing calls](https://help.twilio.com/articles/223179848-Using-a-non-Twilio-number-as-the-caller-ID-for-outgoing-calls)